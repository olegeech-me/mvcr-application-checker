"""
Read requests from the message queue, collect application status, post status update message
"""

import logging
import signal
import asyncio
import uvloop

from fetcher.config import URL, RABBIT_HOST, RABBIT_SSL_PORT, RABBIT_USER, RABBIT_PASSWORD
from fetcher.config import RABBIT_SSL_CACERTFILE, RABBIT_SSL_CERTFILE, RABBIT_SSL_KEYFILE
from fetcher.config import COOL_OFF_DURATION
from fetcher.browser import Browser
from fetcher.messaging import Messaging
from fetcher.application_processor import ApplicationProcessor


# Set up logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


# Set up SSL params for RabbitMQ
def rabbit_ssl_params():
    if all([RABBIT_SSL_PORT, RABBIT_SSL_CACERTFILE, RABBIT_SSL_CERTFILE, RABBIT_SSL_KEYFILE]):
        return {
            "ssl_port": RABBIT_SSL_PORT,
            "cafile": RABBIT_SSL_CACERTFILE,
            "certfile": RABBIT_SSL_CERTFILE,
            "keyfile": RABBIT_SSL_KEYFILE,
        }
    else:
        return None


# Consume messages in the background, enter cool-off when hit max message limit
async def manage_consumption(processor, messaging_instance):
    fetch_consumer = None
    refresh_consumer = None

    async def start_consumers():
        nonlocal fetch_consumer, refresh_consumer
        if not fetch_consumer:
            fetch_consumer = await messaging_instance.consume_messages("ApplicationFetchQueue", processor.fetch_callback)
        if not refresh_consumer:
            refresh_consumer = await messaging_instance.consume_messages("RefreshStatusQueue", processor.refresh_callback)

    async def stop_consumers():
        nonlocal fetch_consumer, refresh_consumer
        logger.info("Stopping consumers")
        if fetch_consumer:
            fetch_consumer.cancel()
            fetch_consumer = None
            logger.info("Fetch consumer stopped")
        if refresh_consumer:
            refresh_consumer.cancel()
            refresh_consumer = None
            logger.info("Refresh consumer stopped")

    while True:
        if not processor.in_cool_off.is_set():
            await start_consumers()
            await asyncio.sleep(1)
        else:
            await stop_consumers()
            logger.info("Cool-off mode activated. Pausing message consumption.")
            await asyncio.sleep(COOL_OFF_DURATION)
            logger.info("Cool-off mode deactivated. Resuming message consumption.")
            processor.in_cool_off.clear()


async def main():
    """Connect to the message queue, run fetch for the application data, post back status"""
    # Set up shutdown event
    shutdown_event = asyncio.Event()

    browser_instance = Browser()
    messaging_instance = Messaging(RABBIT_HOST, RABBIT_USER, RABBIT_PASSWORD)
    processor = ApplicationProcessor(messaging=messaging_instance, browser=browser_instance, url=URL)

    # Register the signal handlers
    signal.signal(signal.SIGINT, lambda s, f: shutdown_event.set())
    signal.signal(signal.SIGTERM, lambda s, f: shutdown_event.set())

    # Connect to RabbitMQ & set up queues
    await messaging_instance.connect(ssl_params=rabbit_ssl_params())
    await messaging_instance.setup_queues("ApplicationFetchQueue", "StatusUpdateQueue", "RefreshStatusQueue")

    consumption_task = asyncio.ensure_future(manage_consumption(processor, messaging_instance))

    try:
        await shutdown_event.wait()
    finally:
        consumption_task.cancel()
        try:
            await consumption_task
        except asyncio.CancelledError:
            pass
        await processor.shutdown()


if __name__ == "__main__":
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
