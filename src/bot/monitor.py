import asyncio
import logging
from datetime import datetime, timedelta, timezone
from bot.config import (
    REFRESH_PERIOD,
    SCHEDULER_PERIOD,
    NOT_FOUND_REFRESH_PERIOD,
    NOT_FOUND_MAX_DAYS,
    NOTIFY_RETRY_BASE_INTERVAL,
    NOTIFY_RETRY_MAX_INTERVAL,
    NOTIFY_MONITOR_TICK,
    NOTIFY_DELIVERED_RETENTION_DAYS,
    NOTIFY_PENDING_MAX_AGE_DAYS,
)
from bot.utils import generate_oam_full_string, notify_user, user_label_short
from bot import prometheus_metrics

logger = logging.getLogger(__name__)


def compute_next_retry_at(current_attempts, base_interval, max_interval, now=None):
    """Capped exponential backoff: NOW + min(base * 2^current_attempts, max)

    Returns naive UTC — Notifications.next_attempt_at is TIMESTAMP WITHOUT
    TIME ZONE and asyncpg rejects tz-aware values for that column
    """
    if now is None:
        now = datetime.now(timezone.utc)
    delay = min(base_interval * (2**current_attempts), max_interval)
    return (now + timedelta(seconds=delay)).replace(tzinfo=None)


class ApplicationMonitor:
    def __init__(self, db, rabbit):
        self.db = db
        self.rabbit = rabbit
        self.refresh = timedelta(seconds=REFRESH_PERIOD)
        self.not_found_refresh = timedelta(seconds=NOT_FOUND_REFRESH_PERIOD)
        self.not_found_max_age = timedelta(days=NOT_FOUND_MAX_DAYS)
        self.shutdown_event = asyncio.Event()

    async def start(self):
        logger.info(
            f"Application status monitor started, scheduler_interval={SCHEDULER_PERIOD}, "
            f"refresh_interval={REFRESH_PERIOD}, not_found_refresh_interval={NOT_FOUND_REFRESH_PERIOD}, "
            f"not_found_max_age={NOT_FOUND_MAX_DAYS}"
        )

        while not self.shutdown_event.is_set():
            logger.info("Running periodic status checks")
            await self.check_for_updates()
            await self.expire_stale_not_found_applications()
            try:
                await asyncio.wait_for(self.shutdown_event.wait(), timeout=SCHEDULER_PERIOD)
            except asyncio.TimeoutError:
                pass

    async def check_for_updates(self):
        applications_to_update = await self.db.fetch_applications_needing_update(self.refresh, self.not_found_refresh)

        if not applications_to_update:
            logger.info("No applications need status refresh")
        else:
            logger.info(f"{len(applications_to_update)} application(s) need status refresh")

        for app in applications_to_update:
            message = {
                "chat_id": app["chat_id"],
                "username": app["username"],
                "first_name": app["first_name"],
                "last_name": app["last_name"],
                "number": app["application_number"],
                "suffix": app["application_suffix"],
                "type": app["application_type"],
                "year": app["application_year"],
                "source": "zov" if app["application_type"] == "ZOV" else "oam",
                "force_refresh": False,
                "failed": False,
                "request_type": "refresh",
                "last_updated": app["last_updated"].isoformat() if app["last_updated"] else "0",
            }
            oam_full_string = generate_oam_full_string(app)
            label = user_label_short(app["chat_id"], app["username"], app["first_name"])
            logger.info(f"Scheduling status refresh for {oam_full_string}, user: {label}, last_updated: {app['last_updated']}")
            await self.rabbit.publish_message(message, routing_key="RefreshStatusQueue")

    async def expire_stale_not_found_applications(self):
        applications_to_expire = await self.db.fetch_applications_to_expire(self.not_found_max_age)
        if not applications_to_expire:
            logger.debug("No applications to expire")
            return

        logger.info(f"{len(applications_to_expire)} application(s) to expire")
        for app in applications_to_expire:
            message = {
                "application_id": app["application_id"],
                "chat_id": app["chat_id"],
                "username": app["username"],
                "first_name": app["first_name"],
                "last_name": app["last_name"],
                "number": app["application_number"],
                "suffix": app["application_suffix"],
                "type": app["application_type"],
                "year": app["application_year"],
                "source": "zov" if app["application_type"] == "ZOV" else "oam",
                "request_type": "expire",
                "last_updated": app["created_at"].isoformat() if app["created_at"] else "0",
            }
            oam_full_string = generate_oam_full_string(app)
            label = user_label_short(app["chat_id"], app["username"], app["first_name"])
            logger.info(f"Scheduling expiration for {oam_full_string}, user: {label}, created_at: {app['created_at']}")
            await self.rabbit.publish_message(message, routing_key="ExpirationQueue")

    def stop(self):
        self.shutdown_event.set()


class NotificationDispatcher:
    """Drain the Notifications outbox: claim due rows, send, route the verdict

      ok                → mark_delivered
      retryable_gave_up → bump_attempt with capped exponential backoff
      dead_user         → mark_user_inactive; row stays pending so reactivation
                          re-exposes it on a later tick
      permanent_other   → mark_delivered with last_error to break the loop
    """

    def __init__(self, db, bot):
        self.db = db
        self.bot = bot
        self.shutdown_event = asyncio.Event()
        self.wakeup_event = asyncio.Event()

    def wake(self):
        """Signal the dispatcher that a new row was enqueued"""
        self.wakeup_event.set()

    async def start(self):
        logger.info(
            f"Notification dispatcher started, tick={NOTIFY_MONITOR_TICK}s, "
            f"lock_window={NOTIFY_RETRY_BASE_INTERVAL}s, "
            f"backoff base={NOTIFY_RETRY_BASE_INTERVAL}s, max={NOTIFY_RETRY_MAX_INTERVAL}s, "
            f"delivered_retention={NOTIFY_DELIVERED_RETENTION_DAYS}d, "
            f"pending_max_age={NOTIFY_PENDING_MAX_AGE_DAYS}d"
        )
        while not self.shutdown_event.is_set():
            await self.deliver_pending()
            await self.db.purge_old_notifications(
                NOTIFY_DELIVERED_RETENTION_DAYS, NOTIFY_PENDING_MAX_AGE_DAYS,
            )
            self.wakeup_event.clear()
            await self._wait_for_work()

    async def _wait_for_work(self):
        """Block until shutdown, a wake() call, or NOTIFY_MONITOR_TICK"""
        shutdown_task = asyncio.create_task(self.shutdown_event.wait())
        wakeup_task = asyncio.create_task(self.wakeup_event.wait())
        try:
            await asyncio.wait(
                {shutdown_task, wakeup_task},
                timeout=NOTIFY_MONITOR_TICK,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            shutdown_task.cancel()
            wakeup_task.cancel()

    async def deliver_pending(self):
        """Dispatch pending notifications"""

        rows = await self.db.claim_due_notifications(
            limit=100,
            lock_window_seconds=NOTIFY_RETRY_BASE_INTERVAL,
        )
        if not rows:
            logger.debug("No due notifications")
            return
        logger.info(f"Dispatching {len(rows)} notification(s)")
        for row in rows:
            verdict = await notify_user(self.bot, row["chat_id"], row["text"])
            await self._finalize(row, verdict)

    async def _finalize(self, row, verdict):
        notification_id = row["id"]
        chat_id = row["chat_id"]
        notification_kind = row["kind"]
        prometheus_metrics.record_notification(notification_kind, verdict)
        if verdict == "ok":
            await self.db.mark_delivered(notification_id)
        elif verdict == "dead_user":
            # Row stays pending; is_active=FALSE shields it until reactivation
            await self.db.mark_user_inactive(chat_id, "send_message returned dead_user")
        elif verdict == "retryable_gave_up":
            next_at = compute_next_retry_at(
                row["attempts"],
                NOTIFY_RETRY_BASE_INTERVAL,
                NOTIFY_RETRY_MAX_INTERVAL,
            )
            await self.db.bump_attempt(notification_id, next_at, last_error="retryable_gave_up")
        elif verdict == "permanent_other":
            logger.error(
                f"Permanent send error for notification {notification_id}, chat {chat_id}; "
                f"marking delivered to break the loop"
            )
            await self.db.mark_delivered(notification_id, last_error="permanent_other")
        else:
            prometheus_metrics.record_error("notification_dispatcher", "unexpected")
            logger.error(f"Unknown verdict from notify_user: {verdict!r}")

    def stop(self):
        self.shutdown_event.set()


class ReminderMonitor:
    def __init__(self, db, rabbit):
        self.db = db
        self.rabbit = rabbit
        self.shutdown_event = asyncio.Event()

    async def start(self):
        logger.info("Reminder monitor started")
        while not self.shutdown_event.is_set():
            logger.debug("Checking for reminders to execute...")
            await self.trigger_reminders()
            try:
                # Reminders are set with precision to minute
                await asyncio.wait_for(self.shutdown_event.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass

    async def trigger_reminders(self):
        # Fetch reminders that need to be executed at the current time.
        reminders_to_trigger = await self.db.fetch_due_reminders()

        if not reminders_to_trigger:
            logger.debug("No reminders to execute at this time")
        else:
            logger.info(f"{len(reminders_to_trigger)} reminder(s) are due to execute")

        for reminder in reminders_to_trigger:
            message = {
                "chat_id": reminder["chat_id"],
                "username": reminder["username"],
                "first_name": reminder["first_name"],
                "last_name": reminder["last_name"],
                "number": reminder["application_number"],
                "suffix": reminder["application_suffix"],
                "type": reminder["application_type"],
                "year": reminder["application_year"],
                "source": "zov" if reminder["application_type"] == "ZOV" else "oam",
                "force_refresh": True,
                "failed": False,
                "request_type": "fetch",
                "is_reminder": True,
                "last_updated": reminder["last_updated"].isoformat() if reminder["last_updated"] else "0",
            }
            oam_full_string = generate_oam_full_string(reminder)
            label = user_label_short(reminder["chat_id"], reminder["username"], reminder["first_name"])
            logger.info(f"[REMINDER] Force refreshing status for {oam_full_string}, user: {label}")
            await self.rabbit.publish_message(message, routing_key="ApplicationFetchQueue")

    def stop(self):
        self.shutdown_event.set()
