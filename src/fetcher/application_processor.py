import json
import logging
import sys
import asyncio
import random
from fetcher.config import JITTER_SECONDS, MAX_RETRIES

logger = logging.getLogger(__name__)


class ApplicationProcessor:
    def __init__(self, messaging, browser, url):
        self.messaging = messaging
        self.browser = browser
        self.url = url
        self.current_message = None
        self.waiting_refresh_requests = 0
        self.processing_apps = set()
        self.lock = asyncio.Lock()

    async def is_processing(self, app_number):
        """Check if an application number is currently being processed."""
        async with self.lock:
            return app_number in self.processing_apps

    async def start_processing(self, app_number):
        """Mark an application number as currently being processed."""
        async with self.lock:
            self.processing_apps.add(app_number)

    async def end_processing(self, app_number):
        """Mark an application number as done processing."""
        async with self.lock:
            self.processing_apps.remove(app_number)

    async def fetch_callback(self, message):
        """Callback function triggered when a fetch request message is received"""

        self.current_message = message
        try:
            await self.process_fetch_request(message)
        finally:
            self.current_message = None

    async def refresh_callback(self, message):
        """Callback function triggered when a refresh request message is received"""
        self.current_message = message
        try:
            await self.process_refresh_request(message)
        finally:
            self.current_message = None

    async def _manage_failed_request(self, message, queue_name):
        """Handles failed request messages, implementing a retry mechanism or sending a failure message"""

        app_details = json.loads(message.body.decode("utf-8"))
        retry_count = message.headers.get("x-retry-count", 0) + 1

        if retry_count > MAX_RETRIES:
            logger.error(f"Message exceeded max retries: {app_details}")
            app_string = "OAM-{}-{}/{}-{}".format(
                app_details["number"],
                app_details["suffix"],
                app_details["type"],
                app_details["year"],
            )
            app_details["status"] = (
                f"😥Unfortunately, we couldn't get the status of <b>{app_string}</b> application. "
                "Please ensure your application details are correct and try unsubscribing and then subscribing again. "
                "If the issue persists, you can reach out to developers."
            )
            await self.messaging.publish_message("StatusUpdateQueue", app_details)
            await message.ack()
        else:
            logger.info(f"Rescheduling message, x-retry-count: {retry_count}")
            await self.messaging.publish_message(
                queue_name, json.loads(message.body.decode("utf-8")), headers={"x-retry-count": retry_count}
            )
            await message.ack()

    async def process_fetch_request(self, message):
        """Process a fetch request to retrieve the status of an application"""

        app_details = json.loads(message.body.decode("utf-8"))
        logger.info(f"Received fetch request: {app_details}")

        if await self.is_processing(app_details["number"]) and not message.headers.get("x-retry-count"):
            logger.info(
                f"Skipping fetch request for application number {app_details['number']} as it's currently being processed"
            )
            return
        await self.start_processing(app_details["number"])

        app_status = await self.browser.fetch(self.url, app_details)
        if app_status:
            logger.info(f"Fetched status for application number {app_details['number']}")
            app_details["status"] = app_status
            await message.ack()
            await self.messaging.publish_message("StatusUpdateQueue", app_details)
            logger.debug(f"Message was pushed to StateUpdateQueue {app_details['number']}")
        else:
            logger.error(f"Failed to fetch status for application number {app_details['number']}")
            await self._manage_failed_request(message, "ApplicationFetchQueue")

        await self.end_processing(app_details["number"])

    async def process_refresh_request(self, message):
        """Process a refresh request to update the status of an application"""

        app_details = json.loads(message.body.decode())
        logger.info(f"Received refresh request: {app_details}")

        if await self.is_processing(app_details["number"]) and not message.headers.get("x-retry-count"):
            logger.info(
                f"Skipping refresh request for application number {app_details['number']} as it's currently being processed"
            )
            return
        await self.start_processing(app_details["number"])

        # Sleep between 5 to JITTER_SECONDS to avoid getting blocked
        sleep_time = random.randint(5, JITTER_SECONDS)
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
            await self._manage_failed_request(message, "RefreshStatusQueue")

        await self.end_processing(app_details["number"])

    async def shutdown(self):
        if self.current_message:
            logger.info(f"Shuting down: NACK'ing message with delivery_tag: {self.current_message.delivery_tag}")
            await self.current_message.nack()
        logger.info("Shutting down rabbit connection ...")
        await self.messaging.close()
        logger.info("Shutting down browser ...")
        self.browser.close()
        sys.exit(0)
