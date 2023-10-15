import logging
import cachetools
import asyncio

logger = logging.getLogger(__name__)


class Metrics:
    def __init__(self):
        self._bot_data = cachetools.TTLCache(maxsize=10000, ttl=300)
        self._fetcher_data = cachetools.TTLCache(maxsize=10000, ttl=300)
        self.lock = asyncio.Lock()

    async def update_fetcher_metrics(self, fetcher_id, metrics_data):
        """Update metrics for a specific fetcher"""
        logger.debug(f"Updating metrics for {fetcher_id}")
        async with self.lock:
            self._fetcher_data[fetcher_id] = metrics_data

    async def get_fetcher_metrics(self, fetcher_id):
        """Retrieve metrics for a specific fetcher"""
        async with self.lock:
            return self._fetcher_data.get(fetcher_id, None)

    async def get_all_fetcher_metrics(self):
        """Retrieve metrics for all fetchers"""
        async with self.lock:
            return self._fetcher_data

    async def reset_fetcher_metrics(self, fetcher_id):
        """Reset metrics for a specific fetcher"""
        async with self.lock:
            if fetcher_id in self._fetcher_data:
                del self._fetcher_data[fetcher_id]

    async def update_bot_metrics(self, metrics_data):
        """Update metrics for the bot"""
        logger.debug("Updating bot metrics")
        async with self.lock:
            self._bot_data["metrics"] = metrics_data
