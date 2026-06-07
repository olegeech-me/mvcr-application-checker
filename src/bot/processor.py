import logging

from bot import prometheus_metrics
from bot.texts import message_texts
from bot.utils import generate_oam_full_string, user_label, user_label_short
from bot.utils import MVCR_STATUSES, categorize_application_status

logger = logging.getLogger(__name__)


class Processor:
    """Process messages received from RabbitMQ"""

    def __init__(self, db, fetcher_stats, notification_dispatcher):
        self.db = db
        self.fetcher_stats = fetcher_stats
        self.notification_dispatcher = notification_dispatcher

    async def process_status_update(self, msg_data):
        """Process one StatusUpdateQueue message"""
        chat_id = msg_data.get("chat_id")
        received_status = msg_data.get("status")
        if not chat_id or not received_status:
            return "dropped"

        chat_id, number, type_, year = self._application_key(msg_data)
        failed = msg_data.get("failed", False)
        request_type = msg_data.get("request_type")
        current_status = await self.db.fetch_application_status(chat_id, number, type_, year)
        oam_full_string = generate_oam_full_string(msg_data)
        label = self._user_label(msg_data)

        if current_status is None:
            prometheus_metrics.record_error("db", "db_error")
            logger.error(f"Failed to get current status from db for {oam_full_string}, user {label}")
            return "failed"

        if failed and request_type == "refresh":
            # Drop failed refresh requests to avoid mass status rewrites when fetchers have issues
            logger.warning(f"[REFRESH FAILED] Failed to refresh status {oam_full_string}, user {label}")
            return "dropped"

        # FIXME olegeech: should be fixed on the fetcher side
        # sometimes the fetcher returns a status for a different application
        # with the one trailing number off
        # e.g. 1234 instead of 12345
        if number not in received_status:
            logger.warning(f"[NOMATCH] Application number in status {received_status} doesn't match application number {number}")
            return "dropped"

        has_changed = current_status != received_status
        force_refresh = msg_data.get("force_refresh", False)
        if not has_changed and not force_refresh:
            await self._refresh_last_checked(msg_data)
            return "ignored"

        if failed and request_type == "fetch":
            return await self._handle_failed_fetch(msg_data)

        if has_changed:
            return await self._handle_changed_status(msg_data, received_status, force_refresh)

        return await self._handle_force_refresh_unchanged(msg_data, received_status)

    async def process_expiration(self, msg_data):
        """Process one ExpirationQueue message"""
        application_id = msg_data["application_id"]
        chat_id = msg_data["chat_id"]
        oam_full_string = generate_oam_full_string(msg_data)
        label = self._user_label(msg_data)
        logger.info(
            f"[EXPIRE] Application {oam_full_string}, user {label}, created at {msg_data['last_updated']} "
            "has been too long in the state NOT_FOUND, expiring"
        )

        if await self.db.resolve_application(application_id):
            lang = await self.db.fetch_user_language(chat_id)
            text = message_texts[lang]["not_found_expired"].format(app_string=oam_full_string)
            await self._enqueue_notification(
                chat_id,
                kind="expiration",
                text=text,
                origin_ref=application_id,
            )
            return "processed"

        prometheus_metrics.record_error("db", "db_error")
        logger.error(f"Failed to expire application {oam_full_string}, user {label}")
        return "failed"

    async def process_fetcher_stats(self, msg_data):
        """Process one FetcherMetricsQueue message"""
        logger.debug(f"Received metrics message: {msg_data}")
        fetcher_id = msg_data.get("fetcher_id", None)
        if fetcher_id:
            await self.fetcher_stats.update_fetcher_metrics(fetcher_id, msg_data)
            return "processed"

        prometheus_metrics.record_error("rabbitmq", "missing_fetcher_id")
        logger.error(f"Couldn't find fetcher ID in the service message: {msg_data}")
        return "failed"

    async def _refresh_last_checked(self, msg_data):
        oam_full_string = generate_oam_full_string(msg_data)
        short = user_label_short(
            msg_data["chat_id"],
            msg_data.get("username"),
            msg_data.get("first_name"),
        )
        logger.info(f"[REFRESH] Status refreshed for {oam_full_string}, user {short}")
        logger.debug(f"Status didn't change for {oam_full_string}, user {short}")
        await self.db.update_last_checked(*self._application_key(msg_data))

    async def _handle_failed_fetch(self, msg_data):
        chat_id = msg_data["chat_id"]
        oam_full_string = generate_oam_full_string(msg_data)
        label = self._user_label(msg_data)
        received_status = msg_data["status"]
        if msg_data.get("is_reminder", False):
            logger.error(
                f"[REMINDER] Failed to fetch status for {oam_full_string}, "
                f"user {label}, status: {received_status}"
            )
            return "dropped"

        application_id = await self.db.update_application_status(
            *self._application_key(msg_data),
            received_status,
            True,
            "ERROR",
        )
        if application_id is None:
            prometheus_metrics.record_error("db", "db_error")
            return "failed"

        lang = await self.db.fetch_user_language(chat_id)
        logger.warning(f"[FETCH FAILED] Fetch request failed for {oam_full_string}, user {label}")
        text = self._generate_error_message(msg_data, lang)
        await self._enqueue_notification(
            chat_id,
            kind="failed_fetch",
            text=text,
            origin_ref=application_id,
        )
        return "processed"

    async def _handle_changed_status(self, msg_data, received_status, force_refresh):
        chat_id = msg_data["chat_id"]
        oam_full_string = generate_oam_full_string(msg_data)
        label = self._user_label(msg_data)
        category, emoji_sign = categorize_application_status(received_status)
        application_state = category.upper() if category else "UNKNOWN"
        is_resolved = self._is_resolved(received_status)

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
            *self._application_key(msg_data),
            received_status,
            is_resolved,
            application_state,
        )
        if application_id is None:
            prometheus_metrics.record_error("db", "db_error")
            return "failed"

        lang = await self.db.fetch_user_language(chat_id)
        text = self._render_status_notification_text(lang, received_status)
        await self._enqueue_notification(
            chat_id,
            kind="status_change",
            text=text,
            origin_ref=application_id,
        )
        return "processed"

    async def _handle_force_refresh_unchanged(self, msg_data, received_status):
        chat_id = msg_data["chat_id"]
        application_id = await self.db.update_last_checked(*self._application_key(msg_data))
        if application_id is None:
            prometheus_metrics.record_error("db", "db_error")
            return "failed"

        lang = await self.db.fetch_user_language(chat_id)
        text = self._render_status_notification_text(lang, received_status)
        await self._enqueue_notification(
            chat_id,
            kind="force_refresh_unchanged",
            text=text,
            origin_ref=application_id,
        )
        return "processed"

    async def _enqueue_notification(self, chat_id, kind, text, origin_ref):
        await self.db.enqueue_notification(
            chat_id,
            kind=kind,
            text=text,
            origin_ref=origin_ref,
        )
        self.notification_dispatcher.wake()

    def _application_key(self, msg_data):
        return (
            msg_data["chat_id"],
            msg_data["number"],
            msg_data.get("type"),
            int(msg_data["year"]),
        )

    def _user_label(self, msg_data):
        return user_label(
            msg_data["chat_id"],
            msg_data.get("username"),
            msg_data.get("first_name"),
            msg_data.get("last_name"),
        )

    def _is_resolved(self, status):
        """Check if the application was resolved to its final status"""
        final_statuses = MVCR_STATUSES.get("approved")[0] + MVCR_STATUSES.get("denied")[0] + MVCR_STATUSES.get("pre_approved")[0]
        return any(final_status in status for final_status in final_statuses)

    def _generate_error_message(self, app_details, lang):
        """Generate an error message for an application number"""
        app_string = generate_oam_full_string(app_details)

        return message_texts[lang]["application_failed"].format(app_string=app_string)

    def _render_status_notification_text(self, lang, status_text):
        """Render the user-facing notification text for a status change

        Categorises the status and prefixes it with the matching templated message
        or a generic application_updated string if categorisation fails
        """
        category, emoji_sign = categorize_application_status(status_text)
        if not category:
            message = message_texts[lang]["application_updated"]
        else:
            message = message_texts[lang][category].format(status_sign=emoji_sign)
        return f"{message}\n\n{status_text}"
