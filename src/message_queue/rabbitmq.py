import json
import aio_pika
import logging
import os

logger = logging.getLogger(__name__)


class RabbitMQ:
    def __init__(self, app, db):
        self.app = app
        self.db = db

        self.RABBIT_HOST = os.getenv("RABBIT_HOST", "localhost")
        self.RABBIT_USER = os.getenv("RABBIT_USER", "bunny_admin")
        self.RABBIT_PASSWORD = os.getenv("RABBIT_PASSWORD", "password")

        self.connection = None
        self.channel = None
        self.queue = None
        self.default_exchange = None

    async def connect(self):
        """Establishes a connection to RabbitMQ and initializes the channel and queue."""
        self.connection = await aio_pika.connect_robust(f"amqp://{self.RABBIT_USER}:{self.RABBIT_PASSWORD}@{self.RABBIT_HOST}")
        self.channel = await self.connection.channel()
        self.queue = await self.channel.declare_queue("StatusUpdateQueue", durable=True)
        self.default_exchange = self.channel.default_exchange
        logger.info("Connected to RabbitMQ")

    async def on_message(self, message: aio_pika.IncomingMessage):
        """Async function to handle messages from StatusUpdateQueue"""
        async with message.process():
            msg_data = json.loads(message.body.decode("utf-8"))
            logger.info(f"Received status update message: {msg_data}")
            chat_id = msg_data.get("chat_id", None)
            status = msg_data.get("status", None)
            if chat_id and status:
                # Update application status in the DB
                await self.db.update_db_status(chat_id, status)

                # Construct the notification text
                notification_text = f"Your application status has been updated: {status}"

                # Notify the user
                try:
                    await self.app.updater.bot.send_message(chat_id=chat_id, text=notification_text, parse_mode="HTML")
                    logger.info(f"Sent status update to chatID {chat_id}")
                except Exception as e:
                    logger.error(f"Failed to send status update to {chat_id}: {e}")

    async def consume_messages(self):
        """Consumes messages from the queue and handles them using on_message."""
        await self.queue.consume(lambda message: self.on_message(message))
        logger.info("Started consumer")

    async def publish_message(self, message, routing_key="ApplicationFetchQueue"):
        """Publishes a message to a RabbitMQ queue."""
        if not self.default_exchange:
            raise Exception("Cannot publish message: default exchange is not initialized.")

        await self.default_exchange.publish(aio_pika.Message(body=json.dumps(message).encode("utf-8")), routing_key=routing_key)
        logger.info(f"Message published to {routing_key}")

    async def close(self):
        if self.connection:
            logger.info("Shutting down rabbit connection")
            await self.connection.close()
            self.connection = None
