import asyncio
import logging
import os
from datetime import timedelta

logger = logging.getLogger(__name__)

REFRESH_PERIOD = int(os.getenv("REFRESH_PERIOD", 3600))


class ApplicationMonitor:
    def __init__(self, db, rabbit):
        self.db = db
        self.rabbit = rabbit
        self.refresh = timedelta(seconds=REFRESH_PERIOD)
        self.shutdown_event = asyncio.Event()

    async def start(self):
        logger.info(f"Application status monitor started, refresh_interval={REFRESH_PERIOD}")
        while not self.shutdown_event.is_set():
            logger.info("Running periodic status checks")
            await self.check_for_updates()
            try:
                await asyncio.wait_for(
                    self.shutdown_event.wait(), timeout=60
                )  # wait for 60 seconds or until shutdown_event is set
            except asyncio.TimeoutError:
                pass

    async def check_for_updates(self):
        applications_to_update = await self.db.get_applications_needing_update(self.refresh)

        if not applications_to_update:
            logger.info("No applications need status refresh")
        else:
            logger.info(f"{len(applications_to_update)} applications need status refresh")

        for app in applications_to_update:
            message = {
                "chat_id": app["chat_id"],
                "number": app["application_number"],
                "suffix": app["application_suffix"],
                "type": app["application_type"],
                "year": app["application_year"],
            }
            await self.rabbit.publish_message(message)
            logger.info(f"Scheduling status update for application {app['application_number']} user {app['chat_id']}")

    def stop(self):
        self.shutdown_event.set()
