import logging
import cachetools

logger = logging.getLogger(__name__)


class Metrics:
    def __init__(self):
        self._bot_data = {}
        self._fetcher_data = cachetools.TTLCache(maxsize=10000, ttl=300)

    def update_fetcher_metrics(self, fetcher_id, metrics_data):
        """Update metrics for a specific fetcher"""
        logger.debug(f"Updating metrics for {fetcher_id}")
        self._fetcher_data[fetcher_id] = metrics_data

    def get_fetcher_metrics(self, fetcher_id):
        """Retrieve metrics for a specific fetcher"""
        return self._fetcher_data.get(fetcher_id, None)

    def get_all_fetcher_metrics(self):
        """Retrieve metrics for all fetchers"""
        return self._fetcher_data

    def reset_fetcher_metrics(self, fetcher_id):
        """Reset metrics for a specific fetcher"""
        if fetcher_id in self._fetcher_data:
            del self._fetcher_data[fetcher_id]
