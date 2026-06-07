import json
import aio_pika
import asyncio
import logging
import hashlib
import cachetools
from aiormq.exceptions import AMQPConnectionError
from bot import prometheus_metrics
from bot.processor import Processor
from bot.utils import generate_oam_full_string, user_label

MAX_RETRIES = 5  # maximum number of connection retries
RETRY_DELAY = 5  # delay (in seconds) between retries

logger = logging.getLogger(__name__)


class RabbitMQ:
    def __init__(self, host, user, password, bot, db, requeue_ttl, fetcher_stats, loop, notification_dispatcher):
        self.host = host
        self.user = user
        self.password = password
        self.bot = bot
        self.db = db
        self.loop = loop
        # Held to wake the dispatcher after each enqueue so happy-path delivery
        # latency stays sub-second instead of waiting for NOTIFY_MONITOR_TICK
        self.notification_dispatcher = notification_dispatcher
        self.connection = None
        self.channel = None
        self.queue = None
        self.expiration_queue = None
        self.service_queue = None
        self.default_exchange = None
        self.published_messages = cachetools.TTLCache(maxsize=10000, ttl=requeue_ttl)
        self.fetcher_stats = fetcher_stats
        self.processor = Processor(db, fetcher_stats, notification_dispatcher)

    async def connect(self):
        """Establishes a connection to RabbitMQ and initializes the channel and queue."""
        for retry in range(1, MAX_RETRIES + 1):
            try:
                self.connection = await aio_pika.connect_robust(
                    f"amqp://{self.user}:{self.password}@{self.host}",
                    loop=self.loop,
                )
                self.channel = await self.connection.channel()
                self.queue = await self.channel.declare_queue("StatusUpdateQueue", durable=True)
                self.expiration_queue = await self.channel.declare_queue("ExpirationQueue", durable=True)
                self.service_queue = await self.channel.declare_queue("FetcherMetricsQueue", durable=True)
                self.default_exchange = self.channel.default_exchange
                logger.info("Connected to RabbitMQ")
                break  # Exit the loop if connection is successful
            except AMQPConnectionError as e:
                if retry < MAX_RETRIES:
                    logger.warning(f"Error: {e}")
                    logger.warning(f"Connection attempt {retry} failed. Retrying in {RETRY_DELAY} seconds...")
                    await asyncio.sleep(RETRY_DELAY)
                else:
                    logger.error("Max retries reached. Could not connect to RabbitMQ.")
                    prometheus_metrics.record_error("rabbitmq", "connection")
                    raise

    async def close(self):
        if self.connection:
            logger.info("Shutting down rabbit connection")
            await self.connection.close()
            self.connection = None

    async def consume_update_messages(self):
        """Consumes messages with status updates"""
        await self.queue.consume(lambda message: self.on_update_message(message))
        logger.info("Started status updates consumer")

    async def consume_expiration_messages(self):
        """Consumes messages with requests to expire stale NOT_FOUND applications"""
        await self.expiration_queue.consume(lambda message: self.on_expiration_message(message))
        logger.info("Started expiration requests consumer")

    async def consume_service_messages(self):
        """Consumes service messages (fetcher stats )"""
        await self.service_queue.consume(lambda message: self.on_service_message(message))
        logger.info("Started service metrics consumer")

    async def on_update_message(self, message: aio_pika.IncomingMessage):
        """Async function to handle messages from StatusUpdateQueue"""
        async with message.process():
            msg_data = self._decode_message(message)
            logger.debug(f"Received status update message: {msg_data}")

            # Generate unique ID for the consumed message and remove it from published_messages
            unique_id = self.generate_unique_id(msg_data)
            self.discard_message_id(unique_id)
            processor_result = await self.processor.process_status_update(msg_data)

        self._record_message_result("StatusUpdateQueue", processor_result)

    async def on_expiration_message(self, message: aio_pika.IncomingMessage):
        """Async function to handle messages from ExpirationQueue"""
        async with message.process():
            msg_data = self._decode_message(message)
            processor_result = await self.processor.process_expiration(msg_data)

        self._record_message_result("ExpirationQueue", processor_result)

    async def on_service_message(self, message: aio_pika.IncomingMessage):
        """Async function to handle service messages from FetcherMetricsQueue"""
        async with message.process():
            msg_data = self._decode_message(message)
            processor_result = await self.processor.process_fetcher_stats(msg_data)

        self._record_message_result("FetcherMetricsQueue", processor_result)

    async def publish_message(self, message, routing_key="ApplicationFetchQueue"):
        """Publishes a message to fetchers queue, ensuring not to publish duplicates"""
        unique_id = self.generate_unique_id(message)
        oam_full_string = generate_oam_full_string(message)
        label = user_label(
            message["chat_id"],
            message.get("username"),
            message.get("first_name"),
            message.get("last_name"),
        )
        message_tag = (
            f"request_type: {message['request_type']}, {oam_full_string}, "
            f"user: {label}, last_updated: {message['last_updated']}"
        )
        if self.is_message_published(unique_id):
            logger.warning(f"Message {unique_id} {message_tag} has already been published. Skipping.")
            prometheus_metrics.record_published_message(routing_key, "duplicate_skipped")
            return
        if not self.default_exchange:
            prometheus_metrics.record_published_message(routing_key, "failed")
            prometheus_metrics.record_error("rabbitmq", "publish_failed")
            raise Exception("Cannot publish message: default exchange is not initialized.")

        try:
            await self.default_exchange.publish(
                aio_pika.Message(body=json.dumps(message).encode("utf-8")),
                routing_key=routing_key,
            )
        except Exception:
            prometheus_metrics.record_published_message(routing_key, "failed")
            prometheus_metrics.record_error("rabbitmq", "publish_failed")
            raise
        self.mark_message_as_published(unique_id)
        prometheus_metrics.record_published_message(routing_key, "published")
        logger.debug(f"Message {unique_id} {message_tag} has been published to {routing_key}")

    def generate_unique_id(self, message):
        """Generate a unique ID for a given message"""
        uid_string = (
            f"{message['request_type']}_{message['chat_id']}_{message['number']}_"
            f"{message['type']}_{message['year']}_{message['last_updated']}"
        )
        return hashlib.md5(uid_string.encode()).hexdigest()

    def is_message_published(self, unique_id):
        """Check if a message with the given unique ID has been published"""
        return unique_id in self.published_messages

    def mark_message_as_published(self, unique_id):
        """Mark the message with the given unique ID as published"""
        self.published_messages[unique_id] = True

    def discard_message_id(self, unique_id):
        """Discard the message ID if it exists"""
        if unique_id in self.published_messages:
            self.published_messages.pop(unique_id, None)
            logger.debug(f"Reply received for message ID {unique_id}")

    def _decode_message(self, message):
        """Decode RabbitMQ message body as JSON"""
        return json.loads(message.body.decode("utf-8"))

    def _record_message_result(self, queue_name, processor_result):
        prometheus_metrics.record_rabbitmq_message(queue_name, processor_result)
