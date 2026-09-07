import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fetcher.metrics_collector import MetricsCollector


@pytest.fixture
def collector():
    return MetricsCollector(
        fetcher_id="test-fetcher",
        messaging=AsyncMock(),
        url="https://example.test/",
        send_interval=1,
    )


class _FakeResponse:
    def __init__(self, status):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, get_result):
        self._get_result = get_result
        self.get = MagicMock(return_value=get_result)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_get_website_latency_success(collector):
    session = _FakeSession(_FakeResponse(200))
    with patch("fetcher.metrics_collector.aiohttp.ClientSession", return_value=session) as session_cls:
        with patch("fetcher.metrics_collector.prometheus_metrics") as metrics:
            await collector.get_website_latency()

    assert collector.connection_status == "✅ Connected"
    metrics.set_target_up.assert_called_with(True)
    timeout = session_cls.call_args.kwargs["timeout"]
    assert timeout.total == 15
    assert timeout.connect == 5


@pytest.mark.asyncio
async def test_get_website_latency_timeout(collector):
    session = _FakeSession(_FakeResponse(200))
    session.get = MagicMock(side_effect=asyncio.TimeoutError())

    with patch("fetcher.metrics_collector.aiohttp.ClientSession", return_value=session):
        with patch("fetcher.metrics_collector.prometheus_metrics") as metrics:
            await collector.get_website_latency()

    assert collector.connection_status == "⚠️ Connection Failed"
    metrics.set_target_up.assert_called_with(False)
    metrics.record_error.assert_called_with("latency_check", "timeout")
