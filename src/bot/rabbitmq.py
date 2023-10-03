import json
import aio_pika
import asyncio
import logging
import hashlib
from aiormq.exceptions import AMQPConnectionError
from bot.texts import message_texts

MAX_RETRIES = 5  # maximum number of connection retries
RETRY_DELAY = 5  # delay (in seconds) between retries
FINAL_STATUSES = ["bylo <b>povoleno</b>", "pokud bylo vaše řízení povoleno", "nebylo", "nepovoleno"]

logger = logging.getLogger(__name__)


class RabbitMQ:
    def __init__(self, host, user, password, bot, db, loop):
        self.host = host
        self.user = user
        self.password = password
        self.bot = bot
        self.db = db
        self.loop = loop
        self.published_messages = set()
        self.connection = None
        self.channel = None
        self.queue = None
        self.default_exchange = None

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
                    raise

    def generate_unique_id(self, message):
        """Generate a unique ID for a given message"""
        uid_string = f"{message['chat_id']}_{message['number']}_{message['last_updated']}"
        return hashlib.md5(uid_string.encode()).hexdigest()

    def is_message_published(self, unique_id):
        """Check if a message with the given unique ID has been published"""
        return unique_id in self.published_messages

    def mark_message_as_published(self, unique_id):
        """Mark the message with the given unique ID as published"""
        self.published_messages.add(unique_id)

    def discard_message_id(self, unique_id):
        """Discard the message ID if it exists"""
        if unique_id in self.published_messages:
            self.published_messages.remove(unique_id)
            logger.info(f"Reply received for message ID {unique_id}")

    def is_resolved(self, status):
        """Check if the application was resolved to its final status"""
        return any(phrase in status for phrase in FINAL_STATUSES)

    def _generate_error_message(self, app_details, lang):
        """Generate an error message for an application number"""
        if app_details["suffix"] != "0":
            app_string = "OAM-{}-{}/{}-{}".format(
                app_details["number"], app_details["suffix"], app_details["type"], app_details["year"]
            )
        else:
            app_string = "OAM-{}/{}-{}".format(app_details["number"], app_details["type"], app_details["year"])

        return message_texts[lang]["application_failed"].format(app_string=app_string)

    async def on_message(self, message: aio_pika.IncomingMessage):
        """Async function to handle messages from StatusUpdateQueue"""
        async with message.process():
            msg_data = json.loads(message.body.decode("utf-8"))
            logger.info(f"Received status update message: {msg_data}")
            chat_id = msg_data.get("chat_id", None)
            received_status = msg_data.get("status", None)
            force_refresh = msg_data.get("force_refresh", False)
            failed = msg_data.get("failed", False)
            request_type = msg_data.get("request_type", None)

            # Generate unique ID for the consumed message and remove it from published_messages
            unique_id = self.generate_unique_id(msg_data)
            self.discard_message_id(unique_id)

            if chat_id and received_status:
                # Fetch the current status from the database
                current_status = await self.db.get_application_status(chat_id)

                if current_status is None:
                    logger.error(f"Failed to get current status from db for user {chat_id}")
                    return

                if failed and request_type == "refresh":
                    # Drop failed refresh requests with log message
                    # But do not update status in DB to avoid mass status rewrite
                    # in case of issues at fetcher
                    logger.warning(f"Failed to refresh status for user {chat_id}")
                    return

                # FIXME olegeech: should be fixed on the fetcher side
                # sometimes the fetcher returns a status for a different application
                # with the one trailing number off
                # e.g. 1234 instead of 12345
                if msg_data["number"] not in msg_data["status"]:
                    logger.warning(
                        f"Application number in status {msg_data['status']} doesn't match application number {msg_data['number']}"
                    )
                    return

                if current_status == received_status and not force_refresh:
                    logger.info(f"Status didn't change for user {chat_id} application")
                    await self.db.update_timestamp(chat_id)
                    return

                if failed and request_type == "fetch":
                    is_resolved = True
                else:
                    is_resolved = self.is_resolved(received_status)

                if force_refresh:
                    logger.info(f"Received force refresh response, notifying user {chat_id}")
                else:
                    logger.info(f"Status of application has changed, notifying user {chat_id}")

                # update application status in the DB
                if await self.db.update_db_status(chat_id, received_status, is_resolved):
                    lang = await self.db.get_user_language(chat_id)

                    # if fetch request failed miserably
                    if failed and request_type == "fetch":
                        logger.warning(f"Failed fetch status for user {chat_id}")
                        notification_text = self._generate_error_message(msg_data, lang)

                    # construct the notification text
                    if is_resolved and not failed:
                        notification_text = f"{message_texts[lang]['application_resolved']}\n\n{received_status}"
                        logger.info(f"Application for user {chat_id} has been resolved to {received_status}")

                    # handle force refresh cases
                    if not is_resolved and force_refresh and not failed:
                        notification_text = f"{message_texts[lang]['current_status']} {received_status}"
                    elif not is_resolved and not force_refresh and not failed:
                        notification_text = f"{message_texts[lang]['application_updated']}\n\n{received_status}"

                    # notify the user
                    try:
                        await self.bot.updater.bot.send_message(chat_id=chat_id, text=notification_text)
                        logger.info(f"Sent status update to chatID {chat_id}")
                    except Exception as e:
                        logger.error(f"Failed to send status update to {chat_id}: {e}")

    async def consume_messages(self):
        """Consumes messages from the queue and handles them using on_message."""
        await self.queue.consume(lambda message: self.on_message(message))
        logger.info("Started consumer")

    async def publish_message(self, message, routing_key="ApplicationFetchQueue"):
        """Publishes a message to fetchers queue, ensuring not to publish duplicates"""
        unique_id = self.generate_unique_id(message)
        if self.is_message_published(unique_id):
            logger.info(f"Message {unique_id} has already been published. Skipping.")
            return
        if not self.default_exchange:
            raise Exception("Cannot publish message: default exchange is not initialized.")

        await self.default_exchange.publish(aio_pika.Message(body=json.dumps(message).encode("utf-8")), routing_key=routing_key)
        self.mark_message_as_published(unique_id)
        logger.info(f"Message {unique_id} has been published to {routing_key}")

    async def close(self):
        if self.connection:
            logger.info("Shutting down rabbit connection")
            await self.connection.close()
            self.connection = None
