import aiohttp
import asyncio
import time
import logging
from collections import deque

logger = logging.getLogger(__name__)


class MetricsCollector:
    def __init__(self, fetcher_id, messaging, url, max_latencies=5, duration=1800):  # default is 30 minutes
        self.fetcher_id = fetcher_id
        self.messaging = messaging
        self.url = url
        self.duration = duration
        self.latency_data = deque(maxlen=max_latencies)
        self.fetch_status = {"success": deque(), "failed": deque(), "retried": deque()}
        self.request_state = {"waiting": 0, "locked": 0}
        self.last_report_time = time.time()

    async def get_website_latency(self):
        """Measure latency to the target website"""
        start_time = time.time()
        async with aiohttp.ClientSession() as session:
            async with session.get(self.url) as response:
                latency = time.time() - start_time
                self.record_latency(latency)
                if response.status != 200:
                    logger.error(f"HTTP latency checked failed: {response.status}")

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
        past_time = current_time - self.duration

        recent_successes = len([t for t in self.fetch_status["success"] if t >= past_time])
        recent_failures = len([t for t in self.fetch_status["failed"] if t >= past_time])
        recent_retries = len([t for t in self.fetch_status["retried"] if t >= past_time])

        rates = {
            "success_rate": recent_successes / (self.duration / 60),  # per minute
            "failure_rate": recent_failures / (self.duration / 60),
            "retry_rate": recent_retries / (self.duration / 60),
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
        }

    async def send_metrics(self, interval=60):
        while True:
            await asyncio.sleep(interval)
            await self.get_website_latency()
            metrics = self.get_metrics()
            logger.info(f"Sending metrics: {metrics}")
            await self.messaging.publish_service_message(metrics)
