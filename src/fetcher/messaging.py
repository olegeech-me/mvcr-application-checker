import aio_pika
import asyncio
import json
import logging
import ssl
from fetcher.config import MAX_MESSAGES
from aiormq.exceptions import AMQPConnectionError

MAX_RETRIES = 25  # maximum number of connection retries
RETRY_DELAY = 5  # delay (in seconds) between retries

logger = logging.getLogger(__name__)


class Messaging:
    def __init__(self, host, user, password):
        self.host = host
        self.user = user
        self.password = password
        self.port = 5672
        self.connection = None
        self.channel = None
        self.queues = {}
        self.consumers = {}

    def _create_ssl_context(self, ssl_params):
        """Create an SSL context based on provided parameters"""
        logger.info(f"Rabbit connection ssl_params: {ssl_params}")
        context = ssl.create_default_context(cafile=ssl_params["cafile"])
        context.load_cert_chain(certfile=ssl_params["certfile"], keyfile=ssl_params["keyfile"])
        context.verify_mode = ssl.CERT_REQUIRED
        return context

    async def connect(self, ssl_params=None):
        """Establish a connection to the message broker"""

        if ssl_params:
            ssl_context = self._create_ssl_context(ssl_params)
            self.port = ssl_params["ssl_port"]
        else:
            ssl_context = None

        scheme = "amqps" if ssl_params else "amqp"
        conn_url = f"{scheme}://{self.user}:{self.password}@{self.host}:{self.port}/"

        for retry in range(1, MAX_RETRIES + 1):
            try:
                logger.info(f"Connecting to {self.host} ...")
                self.connection = await aio_pika.connect_robust(
                    conn_url,
                    ssl_context=ssl_context,
                    heartbeat=60,
                    timeout=30
                )
                self.channel = await self.connection.channel()
                await self.channel.set_qos(prefetch_count=MAX_MESSAGES)
                logger.info(f"Connected to the RabbitMQ server at {self.host}")
                break  # Exit the loop if connection is successful
            except AMQPConnectionError as e:
                if retry < MAX_RETRIES:
                    logger.warning(f"Error: {e}")
                    logger.warning(f"Connection attempt {retry} failed. Retrying in {RETRY_DELAY} seconds...")
                    await asyncio.sleep(RETRY_DELAY)
                else:
                    logger.error("Max retries reached. Could not connect to RabbitMQ.")
                    raise

    async def setup_queues(self, **queues):
        """Declare necessary queues and thier durability"""
        for queue_name, durable in queues.items():
            queue = await self.channel.declare_queue(queue_name, durable=durable)
            self.queues[queue_name] = queue

    async def publish_message(self, queue_name, message_body, headers=None):
        """Publish a message to the specified queue"""
        message = aio_pika.Message(body=json.dumps(message_body).encode(), headers=headers)
        await self.connection.default_exchange.publish(message, routing_key=queue_name)
        logger.debug(f"Successfully published message to {queue_name}")

    async def publish_service_message(self, message_body, queue_name="FetcherMetricsQueue", expiration=30, headers=None):
        """Publish a short-lived service message"""
        message = aio_pika.Message(body=json.dumps(message_body).encode(), expiration=expiration, headers=headers)
        await self.connection.default_exchange.publish(message, routing_key=queue_name)
        logger.debug(f"Successfully published message to {queue_name}")

    async def consume_messages(self, queue_name, callback_func):
        """Consume messages from the specified queue"""
        queue = self.queues.get(queue_name)
        if not queue:
            queue = await self.channel.declare_queue(queue_name, durable=True)
            self.queues[queue_name] = queue

        consumer_tag = await queue.consume(callback_func)
        # internally used by aio_pika to keep track of consumers
        self.consumers[queue_name] = (queue, consumer_tag)

    async def close(self):
        """Close the connection"""
        if self.channel:
            logger.info("Closing RabbitMQ channel...")
            await self.channel.close()
        if self.connection:
            logger.info("Closing RabbitMQ connection...")
            await self.connection.close()
