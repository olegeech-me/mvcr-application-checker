import asyncio
import logging
import time
from collections import deque

import aiohttp

from fetcher import prometheus_metrics
from fetcher.config import FULL_VERSION, LATENCY_CHECK_TIMEOUT_SECONDS

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
        self.connection_status = "❓ Unknown"
        self.last_report_time = time.time()
        self.start_time = time.time()

    async def get_website_latency(self):
        """Measure latency to the target website"""
        start_time = time.time()
        try:
            timeout = aiohttp.ClientTimeout(total=LATENCY_CHECK_TIMEOUT_SECONDS, connect=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(self.url) as response:
                    latency = time.time() - start_time
                    self.record_latency(latency)
                    if response.status == 200:
                        self.connection_status = "✅ Connected"
                        prometheus_metrics.set_target_up(True)
                    else:
                        self.connection_status = f"❌ Failed (HTTP {response.status})"
                        prometheus_metrics.set_target_up(False)
                        prometheus_metrics.record_error("latency_check", "unexpected")
                        logger.error(f"HTTP latency check failed: {response.status}")
        except (asyncio.TimeoutError, aiohttp.ServerTimeoutError) as e:
            self.connection_status = "⚠️ Connection Failed"
            latency = time.time() - start_time
            prometheus_metrics.set_target_latency(latency)
            prometheus_metrics.set_target_up(False)
            prometheus_metrics.record_error("latency_check", "timeout")
            logger.error(
                "Latency check timed out for %s. Error: %r. Latency: %ss",
                self.url, e, latency,
            )
        except aiohttp.ClientConnectorError as e:
            self.connection_status = "⚠️ Connection Failed"
            latency = time.time() - start_time
            prometheus_metrics.set_target_latency(latency)
            prometheus_metrics.set_target_up(False)
            prometheus_metrics.record_error("latency_check", "connection")
            logger.error(
                "Failed to connect to %s. Error: %r. Latency: %ss",
                self.url, e, latency,
            )
        except Exception as e:
            self.connection_status = "🚨 Error"
            latency = time.time() - start_time
            prometheus_metrics.set_target_latency(latency)
            prometheus_metrics.set_target_up(False)
            prometheus_metrics.record_error("latency_check", "unexpected")
            logger.error(
                "Unexpected latency check error for %s. Error: %r. Latency: %ss",
                self.url, e, latency,
            )

    def record_latency(self, latency):
        """Record the latency data"""
        self.latency_data.append(latency)
        prometheus_metrics.set_target_latency(latency)

    def get_avg_latency(self):
        """Get average latency from the recorded data"""
        if self.latency_data:
            return sum(self.latency_data) / len(self.latency_data)
        return 0

    def increment_request_state(self, status):
        """Increment the specified request status"""
        if status in self.request_state:
            self.request_state[status] += 1
            prometheus_metrics.set_request_state(status, self.request_state[status])

    def decrement_request_state(self, status):
        """Decrement the specified request status"""
        if status in self.request_state:
            self.request_state[status] -= 1
            prometheus_metrics.set_request_state(status, self.request_state[status])

    def record_fetch_status(self, status):
        """Record fetch status timestamp (either success or failed)"""
        if status in self.fetch_status:
            current_time = time.time()
            self.fetch_status[status].append(current_time)
            if status == "success":
                prometheus_metrics.mark_success(current_time)

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
            "connection_status": self.connection_status,
            "average_latency": self.get_avg_latency(),
            "fetch_status": {"success": recent_successes, "failed": recent_failures, "retries": recent_retries},
            "request_state": self.request_state,
            "rates": rates,
            "rate_interval": self.rate,
            "ttl": self.ttl,
            "uptime": uptime,
            "version": FULL_VERSION,
            "reported_at": current_time,
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
