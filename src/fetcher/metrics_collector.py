import aiohttp
import asyncio
import time
import logging
from collections import deque

logger = logging.getLogger(__name__)


class MetricsCollector:
    def __init__(self, fetcher_id, messaging, url, max_latencies=5, ttl=1800, rate=600, send_interval=60):
        self.fetcher_id = fetcher_id
        self.messaging = messaging
        self.url = url
        self.ttl = ttl
        self.rate = rate
        self.send_interval = send_interval
        self.latency_data = deque(maxlen=max_latencies)
        self.fetch_status = {"success": deque(), "failed": deque(), "retried": deque()}
        self.request_state = {"waiting": 0, "locked": 0}
        self.last_report_time = time.time()
        self.start_time = time.time()

    async def get_website_latency(self):
        """Measure latency to the target website"""
        start_time = time.time()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.url) as response:
                    latency = time.time() - start_time
                    self.record_latency(latency)
                    if response.status != 200:
                        logger.error(f"HTTP latency checked failed: {response.status}")
        except aiohttp.client_exceptions.ClientConnectorError as e:
            latency = time.time() - start_time
            logger.error(f"Failed to connect to {self.url}. Error: {e}. Latency: {latency}s")
        except Exception as e:
            latency = time.time() - start_time
            logger.error(f"An unexpected error occurred while connecting to {self.url}. Error: {e}. Latency: {latency}s")

    def record_latency(self, latency):
        """Record the latency data"""
        self.latency_data.append(latency)

    def get_avg_latency(self):
        """Get average latency from the recorded data"""
        if self.latency_data:
            return sum(self.latency_data) / len(self.latency_data)
        return 0

    def increment_request_state(self, status):
        """Increment the specified request status"""
        if status in self.request_state:
            self.request_state[status] += 1

    def decrement_request_state(self, status):
        """Decrement the specified request status"""
        if status in self.request_state:
            self.request_state[status] -= 1

    def record_fetch_status(self, status):
        """Record fetch status timestamp (either success or failed)"""
        if status in self.fetch_status:
            self.fetch_status[status].append(time.time())

    def get_metrics(self):
        """Retrieve the collected metrics"""
        current_time = time.time()
        past_time = current_time - self.ttl
        uptime = current_time - self.start_time

        recent_successes = len([t for t in self.fetch_status["success"] if t >= past_time])
        recent_failures = len([t for t in self.fetch_status["failed"] if t >= past_time])
        recent_retries = len([t for t in self.fetch_status["retried"] if t >= past_time])

        rates = {
            "success_rate": recent_successes / (self.ttl / self.rate),
            "failure_rate": recent_failures / (self.ttl / self.rate),
            "retry_rate": recent_retries / (self.ttl / self.rate),
        }

        # Remove entries older than the report period
        for state in self.fetch_status.keys():
            while self.fetch_status[state] and self.fetch_status[state][0] < past_time:
                self.fetch_status[state].popleft()

        return {
            "fetcher_id": self.fetcher_id,
            "average_latency": self.get_avg_latency(),
            "fetch_status": {"success": recent_successes, "failed": recent_failures, "retries": recent_retries},
            "request_state": self.request_state,
            "rates": rates,
            "rate_interval": self.rate,
            "ttl": self.ttl,
            "uptime": uptime,
        }

    async def send_metrics(self):
        while True:
            await asyncio.sleep(self.send_interval)
            await self.get_website_latency()
            metrics = self.get_metrics()
            logger.debug(f"Sending metrics: {metrics}")
            try:
                await self.messaging.publish_service_message(metrics)
            except Exception as e:
                logger.error(f"Failed to send metrics: {e}")
