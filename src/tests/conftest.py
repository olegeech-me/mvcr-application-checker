import os
import json
from datetime import datetime
from unittest.mock import Mock, AsyncMock

os.environ["RUN_MODE"] = "TEST"
os.environ["ADMIN_CHAT_IDS"] = "1234567, 56745679"

collect_ignore_glob = ["manual/*"]

from bot.rabbitmq import RabbitMQ  # noqa: E402
from bot.database import Database  # noqa: E402
from fetcher.application_processor import ApplicationProcessor  # noqa: E402


class FakeAcquire:
    """Async context manager that yields a mock connection"""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        pass


def make_db_with_mock_pool():
    """Create a Database instance with an injected mock pool + connection"""
    db = Database("testdb", "user", "pass", "localhost", 5432, None)
    conn = AsyncMock()
    pool = Mock()
    pool.acquire = Mock(return_value=FakeAcquire(conn))
    db.pool = pool
    return db, conn


def make_rabbit():
    """Create a RabbitMQ instance with mocked bot, db, fetcher stats, dispatcher, and exchange"""
    bot = Mock()
    db = AsyncMock()
    fetcher_stats = Mock()
    fetcher_stats.update_fetcher_metrics = AsyncMock()
    dispatcher = Mock()
    rabbit = RabbitMQ("host", "user", "pass", bot, db, 300, fetcher_stats, None, dispatcher)
    rabbit.default_exchange = AsyncMock()
    return rabbit


def make_incoming_message(msg_dict, headers=None):
    """Create a mock aio_pika.IncomingMessage with async context manager"""
    msg = Mock()
    msg.body = json.dumps(msg_dict).encode("utf-8")
    msg.headers = headers or {}
    msg.process = Mock(return_value=AsyncMock())
    msg.ack = AsyncMock()
    return msg


OAM_BASE_MSG = {
    "chat_id": 100,
    "username": "testuser",
    "first_name": "Test",
    "last_name": "User",
    "number": "12345",
    "suffix": "0",
    "type": "TP",
    "year": 2023,
    "force_refresh": False,
    "failed": False,
    "request_type": "refresh",
    "last_updated": "2023-01-01T00:00:00",
}

ZOV_BASE_MSG = {
    "chat_id": 200,
    "username": "zovuser",
    "first_name": "Zov",
    "last_name": "Test",
    "number": "ISTA202504220001",
    "suffix": "0",
    "type": "ZOV",
    "year": 0,
    "source": "zov",
    "force_refresh": False,
    "failed": False,
    "request_type": "refresh",
    "last_updated": "2025-04-22T00:00:00",
}


def make_oam_db_row(**overrides):
    """Build a fake DB row dict that looks like what the monitor queries return"""
    row = {
        "chat_id": 100,
        "username": "testuser",
        "first_name": "Test",
        "last_name": "User",
        "application_number": "12345",
        "application_suffix": "0",
        "application_type": "TP",
        "application_year": 2023,
        "last_updated": datetime(2023, 1, 1),
        "application_state": "IN_PROGRESS",
    }
    row.update(overrides)
    return row


def make_zov_db_row(**overrides):
    """Build a fake DB row for a ZOV application"""
    row = {
        "chat_id": 200,
        "username": "zovuser",
        "first_name": "Zov",
        "last_name": "Test",
        "application_number": "ISTA202504220001",
        "application_suffix": "0",
        "application_type": "ZOV",
        "application_year": 0,
        "last_updated": datetime(2025, 4, 22),
        "application_state": "IN_PROGRESS",
    }
    row.update(overrides)
    return row


def make_processor():
    """Create an ApplicationProcessor with mocked dependencies"""
    messaging = AsyncMock()
    browser = AsyncMock()
    metrics = Mock()
    metrics.increment_request_state = Mock()
    metrics.decrement_request_state = Mock()
    metrics.record_fetch_status = Mock()
    return ApplicationProcessor(messaging, browser, metrics, "http://test.url")


def make_zov_subscription():
    """Build a fake DB subscription row for a ZOV application"""
    return {
        "application_id": 99,
        "application_number": "ISTA202504220001",
        "application_suffix": "0",
        "application_type": "ZOV",
        "application_year": 0,
        "current_status": "Unknown",
        "application_state": "UNKNOWN",
        "is_resolved": False,
    }
