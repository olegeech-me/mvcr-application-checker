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
        self.processing_apps = {"fetch": {}, "refresh": {}}
        self.lock = asyncio.Lock()

    async def is_processing(self, request_type, app_number, app_type, app_year):
        """Check if an application is currently being processed"""
        key = (app_number, app_type, app_year)
        async with self.lock:
            if request_type == "refresh":
                exists = key in self.processing_apps["fetch"] or key in self.processing_apps["refresh"]
            elif request_type == "fetch":
                exists = key in self.processing_apps["fetch"]
            return exists

    async def start_processing(self, request_type, app_number, app_type, app_year):
        """Mark an application as currently being processed"""
        key = (app_number, app_type, app_year)
        async with self.lock:
            logger.info(f"[{app_number}/{app_type}-{app_year}][{request_type.upper()}] Locking for processing")
            self.processing_apps[request_type][key] = True

    async def end_processing(self, request_type, app_number, app_type, app_year):
        """Mark an application as done processing"""
        key = (app_number, app_type, app_year)
        async with self.lock:
            if key in self.processing_apps[request_type]:
                logger.info(f"[{app_number}/{app_type}-{app_year}][{request_type.upper()}] Unlocking, processing finished")
                del self.processing_apps[request_type][key]

    def _get_app_details_from_message(self, message):
        """Extract application details from message body"""
        return json.loads(message.body.decode("utf-8"))

    async def _manage_failed_request(self, message, queue_name):
        """Manage failed requests by rescheduling them or sending an error message"""
        app_details = self._get_app_details_from_message(message)
        retry_count = message.headers.get("x-retry-count", 0) + 1

        if retry_count > MAX_RETRIES:
            logger.error("Message exceeded max retries: %s", app_details)
            app_details["status"] = self._generate_error_message(app_details)
            app_details["failed"] = True
            await self.messaging.publish_message("StatusUpdateQueue", app_details)
            await message.ack()
        else:
            logger.info("Rescheduling message, x-retry-count: %d", retry_count)
            await self.messaging.publish_message(queue_name, app_details, headers={"x-retry-count": retry_count})
            await message.ack()

    def _generate_error_message(self, app_details):
        """Generate an error message for an application number"""
        app_string = "OAM-{}-{}/{}-{} ERROR".format(
            app_details["number"], app_details["suffix"], app_details["type"], app_details["year"]
        )
        return app_string

    async def _process_request(self, message, request_type):
        """Process a fetch or refresh request"""
        retry_count = message.headers.get("x-retry-count")
        app_details = self._get_app_details_from_message(message)
        number = app_details.get("number")
        type_ = app_details.get("type").upper()
        year = app_details.get("year")
        request_type = app_details.get("request_type", "fetch")  # stub for dealing with old format messages in queue
        forced = app_details.get("force_refresh")

        log_prefix_elements = [f"[{number}/{type_}-{year}]", f"[{request_type.upper()}]"]
        if retry_count:
            log_prefix_elements.append(f"[X-RETRY {retry_count}]")
        if forced:
            log_prefix_elements.append("[FORCED]")
        log_prefix = "".join(log_prefix_elements)
        logger.info("%s Received request: %s", log_prefix, app_details)

        if await self.is_processing(request_type, number, type_, year) and not retry_count:
            logger.info(
                "%s Skipping request as it's currently being processed",
                log_prefix,
            )
            await message.ack()
            return

        try:
            await self.start_processing(request_type, number, type_, year)

            if request_type == "refresh" and not retry_count:
                sleep_time = self._get_sleep_time()
                logger.info("%s Sleeping for %d seconds before processing request", log_prefix, sleep_time)

                self.waiting_refresh_requests += 1
                await asyncio.sleep(sleep_time)
                self.waiting_refresh_requests -= 1

            app_status = await self.browser.fetch(self.url, app_details)

            # Check if the app number is not in the received_status
            if app_status and str(number) not in app_status:
                logger.warning(f"{log_prefix} Retrieved status does not match the expected app number. Requeueing...")
                queue_name = "ApplicationFetchQueue" if request_type == "fetch" else "RefreshStatusQueue"
                await self._manage_failed_request(message, queue_name)
            elif app_status:
                logger.info("%s Status update succeeded", log_prefix)
                app_details["status"] = app_status
                await message.ack()
                await self.messaging.publish_message("StatusUpdateQueue", app_details)
                logger.debug("%s Update message was pushed to StateUpdateQueue", log_prefix)
            else:
                logger.error("%s Status update failed", log_prefix)
                queue_name = "ApplicationFetchQueue" if request_type == "fetch" else "RefreshStatusQueue"
                await self._manage_failed_request(message, queue_name)

        except Exception as e:
            logger.error("%s Error processing request: %s", log_prefix, e)
        finally:
            await self.end_processing(request_type, number, type_, year)

    def _get_sleep_time(self):
        """Generate a random sleep time between 5 and JITTER_SECONDS"""
        return random.randint(5, JITTER_SECONDS)

    async def fetch_callback(self, message):
        """Fetch request callback"""
        return await self._process_request(message, "fetch")

    async def refresh_callback(self, message):
        """Refresh request callback"""
        return await self._process_request(message, "refresh")

    async def shutdown(self):
        if self.current_message:
            logger.info("Shuting down: NACK'ing message with delivery_tag: %s", self.current_message.delivery_tag)
            await self.current_message.nack()
        logger.info("Shutting down rabbit connection ...")
        await self.messaging.close()
        logger.info("Shutting down browser ...")
        self.browser.close()
        sys.exit(0)
