import asyncio
import logging
from datetime import timedelta
from bot.loader import REFRESH_PERIOD, SCHEDULER_PERIOD

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
        applications_to_update = await self.db.get_applications_needing_update(self.refresh)

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
                "last_updated": app["last_updated"].isoformat() if app["last_updated"] else "0",
            }
            logger.info(f"Scheduling status update for {app['application_number']} user {app['chat_id']} {app['last_updated']}")
            await self.rabbit.publish_message(message, routing_key="RefreshStatusQueue")

    def stop(self):
        self.shutdown_event.set()
