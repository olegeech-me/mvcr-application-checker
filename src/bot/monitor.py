import asyncio
import logging
from datetime import timedelta
from bot.loader import REFRESH_PERIOD, SCHEDULER_PERIOD
from bot.utils import generate_oam_full_string

logger = logging.getLogger(__name__)


class ApplicationMonitor:
    def __init__(self, db, rabbit):
        self.db = db
        self.rabbit = rabbit
        self.refresh = timedelta(seconds=REFRESH_PERIOD)
        self.shutdown_event = asyncio.Event()

    async def start(self):
        logger.info(
            f"Application status monitor started, refresh_interval={REFRESH_PERIOD}, scheduler_interval={SCHEDULER_PERIOD}"
        )
        while not self.shutdown_event.is_set():
            logger.info("Running periodic status checks")
            await self.check_for_updates()
            try:
                await asyncio.wait_for(self.shutdown_event.wait(), timeout=SCHEDULER_PERIOD)
            except asyncio.TimeoutError:
                pass

    async def check_for_updates(self):
        applications_to_update = await self.db.fetch_applications_needing_update(self.refresh)

        if not applications_to_update:
            logger.info("No applications need status refresh")
        else:
            logger.info(f"{len(applications_to_update)} application(s) need status refresh")

        for app in applications_to_update:
            message = {
                "chat_id": app["chat_id"],
                "number": app["application_number"],
                "suffix": app["application_suffix"],
                "type": app["application_type"],
                "year": app["application_year"],
                "force_refresh": False,
                "failed": False,
                "request_type": "refresh",
                "last_updated": app["last_updated"].isoformat() if app["last_updated"] else "0",
            }
            oam_full_string = generate_oam_full_string(app)
            logger.info(
                f"Scheduling status refresh for {oam_full_string}, user: {app['chat_id']}, last_updated: {app['last_updated']}"
            )
            await self.rabbit.publish_message(message, routing_key="RefreshStatusQueue")

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
            logger.info("No reminders to execute at this time")
        else:
            logger.info(f"{len(reminders_to_trigger)} reminder(s) are due to execute")

        for reminder in reminders_to_trigger:
            message = {
                "chat_id": reminder["chat_id"],
                "number": reminder["application_number"],
                "suffix": reminder["application_suffix"],
                "type": reminder["application_type"],
                "year": reminder["application_year"],
                "force_refresh": True,
                "failed": False,
                "request_type": "fetch",
                "last_updated": reminder["last_updated"].isoformat() if reminder["last_updated"] else "0",
            }
            oam_full_string = generate_oam_full_string(reminder)
            logger.info(f"[REMINDER] Force refreshing status for {oam_full_string}, user: {reminder['chat_id']}")
            await self.rabbit.publish_message(message, routing_key="RefreshStatusQueue")

    def stop(self):
        self.shutdown_event.set()
