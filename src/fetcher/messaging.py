import aio_pika
import asyncio
import json
import logging
import ssl
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

    def _create_ssl_context(self, ssl_params):
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
                self.connection = await aio_pika.connect_robust(conn_url, ssl_context=ssl_context)
                self.channel = await self.connection.channel()
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

    async def setup_queues(self, *queues):
        """Declare necessary queues"""
        for queue_name in queues:
            await self.channel.declare_queue(queue_name, durable=True)

    async def publish_message(self, queue_name, message_body):
        """Publish a message to the specified queue"""
        await self.channel.default_exchange.publish(
            aio_pika.Message(body=json.dumps(message_body).encode()), routing_key=queue_name
        )
        logger.info(f"Successfully published message to {queue_name}")

    async def consume_messages(self, queue_name, callback_func):
        """Consume messages from the specified queue"""
        queue = await self.channel.declare_queue(queue_name, durable=True)
        return await queue.consume(callback_func)

    async def close(self):
        """Close the connection"""
        if self.channel:
            logger.info("Closing RabbitMQ channel...")
            await self.channel.close()
        if self.connection:
            logger.info("Closing RabbitMQ connection...")
            await self.connection.close()
