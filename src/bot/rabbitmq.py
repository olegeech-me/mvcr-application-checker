import json
import aio_pika
import asyncio
import logging
import hashlib
import cachetools
from aiormq.exceptions import AMQPConnectionError
from bot.texts import message_texts
from bot.utils import generate_oam_full_string, user_label, user_label_short
from bot.utils import MVCR_STATUSES, categorize_application_status

MAX_RETRIES = 5  # maximum number of connection retries
RETRY_DELAY = 5  # delay (in seconds) between retries

logger = logging.getLogger(__name__)


def render_status_notification_text(lang, status_text):
    """Render the user-facing notification text for a status change

    Categorises the status and prefixes it with the matching templated message
    (or a generic "application_updated" string if categorisation fails). The
    consumer enqueues this text into Notifications; the dispatcher sends it
    verbatim
    """
    category, emoji_sign = categorize_application_status(status_text)
    if not category:
        message = message_texts[lang]["application_updated"]
    else:
        message = message_texts[lang][category].format(status_sign=emoji_sign)
    return f"{message}\n\n{status_text}"


class RabbitMQ:
    def __init__(self, host, user, password, bot, db, requeue_ttl, metrics, loop, notification_dispatcher):
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
        final_statuses = MVCR_STATUSES.get("approved")[0] + MVCR_STATUSES.get("denied")[0] + MVCR_STATUSES.get("pre_approved")[0]
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
            username = msg_data.get("username")
            first_name = msg_data.get("first_name")
            last_name = msg_data.get("last_name")
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
            label = user_label(chat_id, username, first_name, last_name)

            # Generate unique ID for the consumed message and remove it from published_messages
            unique_id = self.generate_unique_id(msg_data)
            self.discard_message_id(unique_id)

            if chat_id and received_status:
                # Fetch the current status from the database
                current_status = await self.db.fetch_application_status(chat_id, number, type_, year)

                if current_status is None:
                    logger.error(f"Failed to get current status from db for {oam_full_string}, user {label}")
                    return

                has_changed = current_status != received_status

                if failed and request_type == "refresh":
                    # Drop failed refresh requests with log message
                    # But do not update status in DB to avoid mass status rewrite
                    # in case of issues on fetchers
                    logger.warning(f"[REFRESH FAILED] Failed to refresh status {oam_full_string}, user {label}")
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
                    short = user_label_short(chat_id, username, first_name)
                    logger.info(f"[REFRESH] Status refreshed for {oam_full_string}, user {short}")
                    logger.debug(f"Status didn't change for {oam_full_string}, user {short}")
                    await self.db.update_last_checked(chat_id, number, type_, year)
                    return

                # Reminder-triggered fetch failures are dropped silently;
                # initial-fetch failures freeze the row and notify the user.
                if failed and request_type == "fetch":
                    if is_reminder:
                        logger.error(
                            f"[REMINDER] Failed to fetch status for {oam_full_string}, "
                            f"user {label}, status: {received_status}"
                        )
                        return
                    is_resolved = True
                    application_state = "ERROR"
                    application_id = await self.db.update_application_status(
                        chat_id,
                        number,
                        type_,
                        year,
                        received_status,
                        is_resolved,
                        application_state,
                    )
                    if application_id is None:
                        return
                    lang = await self.db.fetch_user_language(chat_id)
                    logger.warning(f"[FETCH FAILED] Fetch request failed for {oam_full_string}, user {label}")
                    text = self._generate_error_message(msg_data, lang)
                    await self.db.enqueue_notification(
                        chat_id,
                        kind="failed_fetch",
                        text=text,
                        origin_ref=application_id,
                    )
                    self.notification_dispatcher.wake()
                    return

                # Categorise the new status (used for application_state and the unrecognised-status warning)
                category, emoji_sign = categorize_application_status(received_status)
                application_state = category.upper() if category else "UNKNOWN"
                is_resolved = self.is_resolved(received_status)

                if force_refresh:
                    logger.info(
                        f"[FORCED] Received force refresh response for {oam_full_string}, "
                        f"user {label}, status: {received_status}"
                    )

                if not category:
                    logger.error(
                        f"[UNRECOGNIZED STATUS] Could not categorize status: {received_status} "
                        f"for application {oam_full_string}, user {label}"
                    )

                if has_changed:
                    if is_resolved:
                        logger.info(
                            f"[RESOLVED][{application_state}] Application {oam_full_string}, "
                            f"user {label} has been resolved to {received_status}"
                        )
                    elif not force_refresh:
                        logger.info(
                            f"[CHANGED][{application_state}] Application status for {oam_full_string}, "
                            f"user {label} has changed to {received_status}"
                        )
                    application_id = await self.db.update_application_status(
                        chat_id,
                        number,
                        type_,
                        year,
                        received_status,
                        is_resolved,
                        application_state,
                    )
                    if application_id is None:
                        return
                    lang = await self.db.fetch_user_language(chat_id)
                    text = render_status_notification_text(lang, received_status)
                    await self.db.enqueue_notification(
                        chat_id,
                        kind="status_change",
                        text=text,
                        origin_ref=application_id,
                    )
                    self.notification_dispatcher.wake()
                else:
                    # force_refresh && status came back identical: refresh last_updated,
                    # acknowledge to the user via the outbox using the same renderer
                    application_id = await self.db.update_last_checked(chat_id, number, type_, year)
                    if application_id is None:
                        return
                    lang = await self.db.fetch_user_language(chat_id)
                    text = render_status_notification_text(lang, received_status)
                    await self.db.enqueue_notification(
                        chat_id,
                        kind="force_refresh_unchanged",
                        text=text,
                        origin_ref=application_id,
                    )
                    self.notification_dispatcher.wake()

    async def on_expiration_message(self, message: aio_pika.IncomingMessage):
        """Async function to handle messages from ExpirationQueue"""
        async with message.process():
            msg_data = json.loads(message.body.decode("utf-8"))
            application_id = msg_data.get("application_id")
            chat_id = msg_data.get("chat_id")
            username = msg_data.get("username")
            first_name = msg_data.get("first_name")
            last_name = msg_data.get("last_name")
            oam_full_string = generate_oam_full_string(msg_data)
            label = user_label(chat_id, username, first_name, last_name)
            logger.info(
                f"[EXPIRE] Application {oam_full_string}, user {label}, created at {msg_data['last_updated']} "
                "has been too long in the state NOT_FOUND, expiring"
            )

            if await self.db.resolve_application(application_id):
                lang = await self.db.fetch_user_language(chat_id)
                text = message_texts[lang]["not_found_expired"].format(app_string=oam_full_string)
                await self.db.enqueue_notification(
                    chat_id,
                    kind="expiration",
                    text=text,
                    origin_ref=application_id,
                )
                self.notification_dispatcher.wake()

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
