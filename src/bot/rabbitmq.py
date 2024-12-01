import json
import aio_pika
import asyncio
import logging
import hashlib
import cachetools
from aiormq.exceptions import AMQPConnectionError
from bot.texts import message_texts
from bot.utils import generate_oam_full_string
from bot.utils import MVCR_STATUSES, categorize_application_status, notify_user

MAX_RETRIES = 5  # maximum number of connection retries
RETRY_DELAY = 5  # delay (in seconds) between retries

logger = logging.getLogger(__name__)


class RabbitMQ:
    def __init__(self, host, user, password, bot, db, requeue_ttl, metrics, loop):
        self.host = host
        self.user = user
        self.password = password
        self.bot = bot
        self.db = db
        self.loop = loop
        self.connection = None
        self.channel = None
        self.queue = None
        self.expiration_queue = None
        self.service_queue = None
        self.default_exchange = None
        self.published_messages = cachetools.TTLCache(maxsize=10000, ttl=requeue_ttl)
        self.metrics = metrics

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
                self.service_queue = await self.channel.declare_queue("FetcherMetricsQueue", durable=False)
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

    def is_resolved(self, status):
        """Check if the application was resolved to its final status"""
        final_statuses = MVCR_STATUSES.get("approved")[0] + MVCR_STATUSES.get("denied")[0]
        return any(final_status in status for final_status in final_statuses)

    def _generate_error_message(self, app_details, lang):
        """Generate an error message for an application number"""
        app_string = generate_oam_full_string(app_details)

        return message_texts[lang]["application_failed"].format(app_string=app_string)

    async def on_update_message(self, message: aio_pika.IncomingMessage):
        """Async function to handle messages from StatusUpdateQueue"""
        async with message.process():
            msg_data = json.loads(message.body.decode("utf-8"))
            logger.debug(f"Received status update message: {msg_data}")
            chat_id = msg_data.get("chat_id", None)
            number = msg_data.get("number", None)
            type_ = msg_data.get("type", None)
            year = int(msg_data.get("year"))
            received_status = msg_data.get("status", None)
            force_refresh = msg_data.get("force_refresh", False)
            failed = msg_data.get("failed", False)
            request_type = msg_data.get("request_type", None)
            is_reminder = msg_data.get("is_reminder", False)
            has_changed = False
            oam_full_string = generate_oam_full_string(msg_data)

            # Generate unique ID for the consumed message and remove it from published_messages
            unique_id = self.generate_unique_id(msg_data)
            self.discard_message_id(unique_id)

            if chat_id and received_status:
                # Fetch the current status from the database
                current_status = await self.db.fetch_application_status(chat_id, number, type_, year)

                if current_status is None:
                    logger.error(f"Failed to get current status from db for {oam_full_string}, user {chat_id}")
                    return

                has_changed = current_status != received_status

                if failed and request_type == "refresh":
                    # Drop failed refresh requests with log message
                    # But do not update status in DB to avoid mass status rewrite
                    # in case of issues on fetchers
                    logger.warning(f"[REFRESH FAILED] Failed to refresh status {oam_full_string}, user {chat_id}")
                    return

                # FIXME olegeech: should be fixed on the fetcher side
                # sometimes the fetcher returns a status for a different application
                # with the one trailing number off
                # e.g. 1234 instead of 12345
                if number not in received_status:
                    logger.warning(
                        f"[NOMATCH] Application number in status {received_status} doesn't match application number {number}"
                    )
                    return

                if not has_changed and not force_refresh:
                    logger.info(f"[REFRESH] Status refreshed for {oam_full_string}, user {chat_id}")
                    logger.debug(f"Status didn't change for {oam_full_string}, user {chat_id}")
                    await self.db.update_last_checked(chat_id, number, type_, year)
                    return

                if failed:
                    if request_type == "fetch":
                        # Tolerate failed fetch requests triggered by reminders, do not notify users / change status in db
                        if is_reminder:
                            logger.error(
                                f"[REMINDER] Failed to to fetch status for {oam_full_string}, "
                                f"user {chat_id}, status: {received_status}"
                            )
                            return
                        else:
                            # If we failed during initial application fetch, further processing is frozen
                            is_resolved = True
                else:
                    is_resolved = self.is_resolved(received_status)

                if force_refresh:
                    logger.info(
                        f"[FORCED] Received force refresh response for {oam_full_string}, "
                        f"user {chat_id}, status: {received_status}"
                    )

                # Get category and status sign
                category, emoji_sign = categorize_application_status(received_status)
                application_state = category.upper() if category else "UNKNOWN"

                # update application status in the DB
                if await self.db.update_application_status(
                    chat_id, number, type_, year, received_status, is_resolved, application_state, has_changed
                ):
                    lang = await self.db.fetch_user_language(chat_id)

                    # if a fetch request failed miserably
                    if failed and request_type == "fetch":
                        logger.warning(f"[FETCH FAILED] Fetch request failed for {oam_full_string}, user {chat_id}")
                        notification_text = self._generate_error_message(msg_data, lang)
                    else:
                        # Log changes only if status has changed
                        if has_changed:
                            if is_resolved:
                                logger.info(
                                    f"[RESOLVED][{application_state}] Application {oam_full_string}, "
                                    f"user {chat_id} has been resolved to {received_status}"
                                )
                            elif not force_refresh:
                                logger.info(
                                    f"[CHANGED][{application_state}] Application status for {oam_full_string},"
                                    f"user {chat_id} has changed to {received_status}"
                                )
                        # Log an error if the status couldn't be categorized on a non-failed update message
                        # Here an Admin should probably take a closer look to wtf is happening, might be
                        # that MVCR's text of response has changed. In any way we let the users know of the change
                        if not category:
                            logger.error(
                                f"[UNRECOGNIZED STATUS] Could not categorize status: {received_status} "
                                f"for application {oam_full_string}, user {chat_id}"
                            )
                            message = message_texts[lang]["application_updated"]
                        else:
                            # Fetch the message's text using category
                            message = message_texts[lang][category].format(status_sign=emoji_sign)

                        notification_text = f"{message}\n\n{received_status}"

                    # notify the user
                    await notify_user(self.bot, chat_id, notification_text)

    async def on_expiration_message(self, message: aio_pika.IncomingMessage):
        """Async function to handle messages from ExpirationQueue"""
        async with message.process():
            msg_data = json.loads(message.body.decode("utf-8"))
            application_id = msg_data.get("application_id")
            chat_id = msg_data.get("chat_id")
            oam_full_string = generate_oam_full_string(msg_data)
            logger.info(
                f"[EXPIRE] Application {oam_full_string}, user {chat_id}, created at {msg_data['last_updated']} "
                "has been too long in the state NOT_FOUND, expiring"
            )

            if await self.db.resolve_application(application_id):
                lang = await self.db.fetch_user_language(chat_id)
                notification_text = message_texts[lang]["not_found_expired"].format(app_string=oam_full_string)

                # notify the user
                await notify_user(self.bot, chat_id, notification_text)

    async def on_service_message(self, message: aio_pika.IncomingMessage):
        """Async function to handle service messages from FetcherMetricsQueue"""
        async with message.process():
            msg_data = json.loads(message.body.decode("utf-8"))
            logger.debug(f"Received metrics message: {msg_data}")
            fetcher_id = msg_data.get("fetcher_id", None)
            if fetcher_id:
                await self.metrics.update_fetcher_metrics(fetcher_id, msg_data)
            else:
                logger.error(f"Couldn't find fetcher ID in the service message: {msg_data}")

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

    async def publish_message(self, message, routing_key="ApplicationFetchQueue"):
        """Publishes a message to fetchers queue, ensuring not to publish duplicates"""
        unique_id = self.generate_unique_id(message)
        oam_full_string = generate_oam_full_string(message)
        message_tag = (
            f"request_type: {message['request_type']}, {oam_full_string}, "
            f"user: {message['chat_id']}, last_updated: {message['last_updated']}"
        )
        if self.is_message_published(unique_id):
            logger.warning(f"Message {unique_id} {message_tag} has already been published. Skipping.")
            return
        if not self.default_exchange:
            raise Exception("Cannot publish message: default exchange is not initialized.")

        await self.default_exchange.publish(aio_pika.Message(body=json.dumps(message).encode("utf-8")), routing_key=routing_key)
        self.mark_message_as_published(unique_id)
        logger.debug(f"Message {unique_id} {message_tag} has been published to {routing_key}")

    async def close(self):
        if self.connection:
            logger.info("Shutting down rabbit connection")
            await self.connection.close()
            self.connection = None
