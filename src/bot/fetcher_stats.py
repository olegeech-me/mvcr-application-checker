import asyncio
import logging

import cachetools

logger = logging.getLogger(__name__)


class FetcherStats:
    def __init__(self):
        self._fetcher_data = cachetools.TTLCache(maxsize=10000, ttl=300)
        self.lock = asyncio.Lock()

    async def update_fetcher_metrics(self, fetcher_id, metrics_data):
        """Update Telegram-facing stats for a specific fetcher"""
        logger.debug(f"Updating fetcher stats for {fetcher_id}")
        async with self.lock:
            self._fetcher_data[fetcher_id] = metrics_data

    async def get_fetcher_metrics(self, fetcher_id):
        """Retrieve stats for a specific fetcher"""
        async with self.lock:
            return self._fetcher_data.get(fetcher_id, None)

    async def get_all_fetcher_metrics(self):
        """Retrieve stats for all fetchers"""
        async with self.lock:
            return self._fetcher_data

    async def reset_fetcher_metrics(self, fetcher_id):
        """Reset stats for a specific fetcher"""
        async with self.lock:
            if fetcher_id in self._fetcher_data:
                del self._fetcher_data[fetcher_id]
