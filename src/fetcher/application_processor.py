import json
import logging
import sys
import asyncio
import random
from fetcher.config import JITTER_SECONDS

logger = logging.getLogger(__name__)


class ApplicationProcessor:
    def __init__(self, messaging, browser, url):
        self.messaging = messaging
        self.browser = browser
        self.url = url
        self.current_message = None
        self.waiting_refresh_requests = 0

    async def fetch_callback(self, message):
        self.current_message = message
        try:
            await self.process_fetch_request(message)
        finally:
            self.current_message = None

    async def refresh_callback(self, message):
        self.current_message = message
        try:
            await self.process_refresh_request(message)
        finally:
            self.current_message = None

    async def process_fetch_request(self, message):
        app_details = json.loads(message.body.decode("utf-8"))
        logger.info(f"Received fetch request: {app_details}")

        app_status = await self.browser.fetch(self.url, app_details)
        if app_status:
            logger.info(f"Fetched status for application number {app_details['number']}")
            app_details["status"] = app_status
            await message.ack()
            await self.messaging.publish_message("StatusUpdateQueue", app_details)
            logger.debug(f"Message was pushed to StateUpdateQueue {app_details['number']}")
        else:
            logger.error(f"Failed to fetch status for application number {app_details['number']}")
            await message.nack()

    async def process_refresh_request(self, message):
        app_details = json.loads(message.body.decode())
        logger.info(f"Received refresh request: {app_details}")

        # Sleep between 60 to JITTER_SECONDS to avoid getting blocked
        sleep_time = random.randint(60, JITTER_SECONDS)
        logger.info(f"Sleeping for {sleep_time} seconds before processing {app_details['number']} refresh request")

        self.waiting_refresh_requests += 1
        await asyncio.sleep(sleep_time)
        self.waiting_refresh_requests -= 1

        app_status = await self.browser.fetch(self.url, app_details)
        if app_status:
            logger.info(f"Refreshed status for application number {app_details['number']}")
            app_details["status"] = app_status
            await message.ack()
            await self.messaging.publish_message("StatusUpdateQueue", app_details)
            logger.debug(f"Message was pushed to StateUpdateQueue {app_details['number']}")
        else:
            logger.error(f"Failed to refresh status for application number {app_details['number']}")
            await message.nack()

    async def shutdown(self):
        if self.current_message:
            logger.info(f"Shuting down: NACK'ing message with delivery_tag: {self.current_message.delivery_tag}")
            await self.current_message.nack()
        logger.info("Shutting down rabbit connection ...")
        await self.messaging.close()
        logger.info("Shutting down browser ...")
        self.browser.close()
        sys.exit(0)
