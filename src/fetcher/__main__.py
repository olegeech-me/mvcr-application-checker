"""
Read requests from the message queue, collect application status, post status update message
"""

import logging
import signal
import asyncio
import uvloop

from fetcher.config import URL, RABBIT_HOST, RABBIT_SSL_PORT, RABBIT_USER, RABBIT_PASSWORD, LOG_LEVEL
from fetcher.config import RABBIT_SSL_CACERTFILE, RABBIT_SSL_CERTFILE, RABBIT_SSL_KEYFILE
from fetcher.config import ID, METRICS_TTL, METRICS_RATE, METRICS_SEND_INTERVAL
from fetcher.browser import Browser
from fetcher.messaging import Messaging
from fetcher.application_processor import ApplicationProcessor
from fetcher.metrics_collector import MetricsCollector


# Set up logging
log_level_int = eval(f"logging.{LOG_LEVEL}")
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=log_level_int)
logger = logging.getLogger(__name__)
logger.setLevel(log_level_int)


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


async def main():
    """Connect to the message queue, run fetch for the application data, post back status"""
    # Set up shutdown event
    shutdown_event = asyncio.Event()

    browser_instance = Browser()
    messaging_instance = Messaging(RABBIT_HOST, RABBIT_USER, RABBIT_PASSWORD)
    metrics_collector = MetricsCollector(
        fetcher_id=ID,
        messaging=messaging_instance,
        url=URL,
        ttl=METRICS_TTL,
        rate=METRICS_RATE,
        send_interval=METRICS_SEND_INTERVAL,
    )
    processor = ApplicationProcessor(messaging=messaging_instance, browser=browser_instance, metrics=metrics_collector, url=URL)

    # Register the signal handlers
    signal.signal(signal.SIGINT, lambda s, f: shutdown_event.set())
    signal.signal(signal.SIGTERM, lambda s, f: shutdown_event.set())

    # Connect to RabbitMQ & set up queues with their respective durability
    await messaging_instance.connect(ssl_params=rabbit_ssl_params())
    await messaging_instance.setup_queues(
        ApplicationFetchQueue=True,
        StatusUpdateQueue=True,
        RefreshStatusQueue=True,
    )
    # not durable for fetcher metric queue
    await messaging_instance.setup_queues(FetcherMetricsQueue=False)

    # Start processing requests in the background
    asyncio.gather(
        messaging_instance.consume_messages("ApplicationFetchQueue", processor.fetch_callback),
        messaging_instance.consume_messages("RefreshStatusQueue", processor.refresh_callback),
        metrics_collector.send_metrics(),
    )

    # Keep the loop running until a shutdown signal is received
    while not shutdown_event.is_set():
        try:
            metrics = metrics_collector.get_metrics()
            logger.info(f"Fetcher metrics: {metrics}")
            await asyncio.wait_for(shutdown_event.wait(), timeout=300)
        except asyncio.TimeoutError:
            pass

    await processor.shutdown()


if __name__ == "__main__":
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
