import json
import logging
import sys
import asyncio
import random
from fetcher.config import MAX_MESSAGES, COOL_OFF_DURATION, JITTER_SECONDS

logger = logging.getLogger(__name__)


class ApplicationProcessor:
    def __init__(self, messaging, browser, url):
        self.messaging = messaging
        self.browser = browser
        self.url = url
        self.current_message = None
        self.in_cool_off = asyncio.Event()
        self.message_count = 0

    async def check_and_trigger_cool_off(self):
        if self.message_count >= MAX_MESSAGES:
            logger.warning(
                f"Fetcher has reached the maximum message limit ({MAX_MESSAGES}). Waiting for {COOL_OFF_DURATION} seconds."
            )
            self.in_cool_off.set()
            self.message_count = 0

    async def fetch_callback(self, message):
        self.current_message = message
        self.message_count += 1
        await self.check_and_trigger_cool_off()
        if self.in_cool_off.is_set():
            logger.info(f"In cool-off mode. Ignoring message with id {message.delivery_tag}")
            return
        try:
            await self.process_fetch_request(message)
        finally:
            self.current_message = None

    async def refresh_callback(self, message):
        self.current_message = message
        self.message_count += 1
        await self.check_and_trigger_cool_off()
        if self.in_cool_off.is_set():
            logger.info(f"In cool-off mode. Ignoring message with id {message.delivery_tag}")
            return
        try:
            await self.process_refresh_request(message)
        finally:
            self.current_message = None

    async def process_fetch_request(self, message):
        app_details = json.loads(message.body.decode("utf-8"))
        logger.info(f"Received fetch request: {app_details}")
        await message.ack()

        app_status = await self.browser.fetch(self.url, app_details)
        if app_status:
            logger.info(f"Fetched status for application number {app_details['number']}")
            app_details["status"] = app_status
            await self.messaging.publish_message("StatusUpdateQueue", app_details)
            logger.debug(f"Message was pushed to StateUpdateQueue {app_details['number']}")
        else:
            logger.error(f"Failed to fetch status for application number {app_details['number']}")
            await message.nack()

    async def process_refresh_request(self, message):
        app_details = json.loads(message.body.decode())
        logger.info(f"Received refresh request: {app_details}")
        await message.ack()

        # Sleep between 5 to JITTER_SECONDS to avoid getting blocked
        sleep_time = random.randint(5, JITTER_SECONDS)
        logger.info(f"Sleeping for {sleep_time} seconds before processing {app_details['number']} refresh request")
        await asyncio.sleep(sleep_time)

        app_status = await self.browser.fetch(self.url, app_details)
        if app_status:
            logger.info(f"Refreshed status for application number {app_details['number']}")
            app_details["status"] = app_status
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
