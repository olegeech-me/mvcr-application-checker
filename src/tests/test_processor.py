from unittest.mock import patch

import pytest
from conftest import OAM_BASE_MSG, make_incoming_message, make_processor

from fetcher.application_processor import MAX_RETRIES, MAX_STATUS_LENGTH

# ---------------------------------------------------------------------------
# Error message generation
# ---------------------------------------------------------------------------


def test_processor_generate_error_message_oam():
    proc = make_processor()
    app_details = {"number": "12345", "suffix": "0", "type": "TP", "year": "2023"}
    result = proc._generate_error_message(app_details)
    assert result == "OAM-12345-0/TP-2023 ERROR"


def test_processor_generate_error_message_zov():
    proc = make_processor()
    app_details = {
        "number": "ISTA202504220001",
        "suffix": "0",
        "type": "ZOV",
        "year": 0,
        "source": "zov",
    }
    result = proc._generate_error_message(app_details)
    assert result == "ISTA202504220001 ERROR"


# ---------------------------------------------------------------------------
# Processing lock lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_processor_lock_lifecycle():
    """start -> is_processing -> end -> not processing"""
    proc = make_processor()

    assert await proc.is_processing("fetch", "12345", "TP", 2023) is False
    await proc.start_processing("fetch", "12345", "TP", 2023)
    assert await proc.is_processing("fetch", "12345", "TP", 2023) is True
    await proc.end_processing("fetch", "12345", "TP", 2023)
    assert await proc.is_processing("fetch", "12345", "TP", 2023) is False


@pytest.mark.asyncio
async def test_processor_refresh_checks_both_queues():
    """refresh is_processing returns True if app is in either fetch or refresh queue"""
    proc = make_processor()

    await proc.start_processing("fetch", "12345", "TP", 2023)
    assert await proc.is_processing("refresh", "12345", "TP", 2023) is True

    await proc.end_processing("fetch", "12345", "TP", 2023)
    await proc.start_processing("refresh", "12345", "TP", 2023)
    assert await proc.is_processing("refresh", "12345", "TP", 2023) is True


# ---------------------------------------------------------------------------
# Prometheus fetch result semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_processor_manage_failed_request_records_retry_metric():
    """A requeued queue-level attempt is retry, not hard failure"""
    proc = make_processor()
    msg = make_incoming_message({**OAM_BASE_MSG, "request_type": "fetch", "source": "oam"})

    with patch("fetcher.application_processor.prometheus_metrics.record_fetch_result") as record_fetch_result:
        await proc._manage_failed_request(msg, "ApplicationFetchQueue")

    proc.messaging.publish_message.assert_awaited_once_with(
        "ApplicationFetchQueue",
        {**OAM_BASE_MSG, "request_type": "fetch", "source": "oam"},
        headers={"x-retry-count": 1},
    )
    msg.ack.assert_awaited_once()
    proc.metrics_collector.record_fetch_status.assert_called_once_with("retried")
    record_fetch_result.assert_called_once_with("fetch", "oam", "retry")


@pytest.mark.asyncio
async def test_processor_manage_failed_request_records_exhausted_failure_metric():
    """Only MAX_RETRIES exhaustion records a hard failed result"""
    proc = make_processor()
    msg = make_incoming_message(
        {**OAM_BASE_MSG, "request_type": "fetch", "source": "oam"},
        headers={"x-retry-count": MAX_RETRIES},
    )

    with patch("fetcher.application_processor.prometheus_metrics.record_fetch_result") as record_fetch_result:
        await proc._manage_failed_request(msg, "ApplicationFetchQueue")

    published_body = proc.messaging.publish_message.call_args.args[1]
    assert proc.messaging.publish_message.call_args.args[0] == "StatusUpdateQueue"
    assert published_body["failed"] is True
    assert published_body["status"] == "OAM-12345-0/TP-2023 ERROR"
    msg.ack.assert_awaited_once()
    proc.metrics_collector.record_fetch_status.assert_called_once_with("failed")
    record_fetch_result.assert_called_once_with("fetch", "oam", "failed")


# ---------------------------------------------------------------------------
# Oversized status guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_processor_oversized_status_requeues_and_records_error():
    """Oversized status text is treated as failed fetch input, not stored downstream"""
    proc = make_processor()
    msg = make_incoming_message({**OAM_BASE_MSG, "request_type": "fetch", "source": "oam"})
    proc.browser.fetch.return_value = "12345 " + ("x" * MAX_STATUS_LENGTH)

    with (
        patch("fetcher.application_processor.prometheus_metrics.record_error") as record_error,
        patch("fetcher.application_processor.prometheus_metrics.record_fetch_result") as record_fetch_result,
    ):
        await proc._process_request(msg, "fetch")

    proc.messaging.publish_message.assert_awaited_once_with(
        "ApplicationFetchQueue",
        {**OAM_BASE_MSG, "request_type": "fetch", "source": "oam"},
        headers={"x-retry-count": 1},
    )
    msg.ack.assert_awaited_once()
    proc.metrics_collector.record_fetch_status.assert_called_once_with("retried")
    record_error.assert_called_once_with("browser", "status_too_large")
    record_fetch_result.assert_called_once_with("fetch", "oam", "retry")


@pytest.mark.asyncio
async def test_processor_oversized_status_exhaustion_records_hard_failure():
    """Repeated oversized status responses eventually become a hard fetch failure"""
    proc = make_processor()
    msg = make_incoming_message(
        {**OAM_BASE_MSG, "request_type": "fetch", "source": "oam"},
        headers={"x-retry-count": MAX_RETRIES},
    )
    proc.browser.fetch.return_value = "12345 " + ("x" * MAX_STATUS_LENGTH)

    with (
        patch("fetcher.application_processor.prometheus_metrics.record_error") as record_error,
        patch("fetcher.application_processor.prometheus_metrics.record_fetch_result") as record_fetch_result,
    ):
        await proc._process_request(msg, "fetch")

    published_body = proc.messaging.publish_message.call_args.args[1]
    assert proc.messaging.publish_message.call_args.args[0] == "StatusUpdateQueue"
    assert published_body["failed"] is True
    assert published_body["status"] == "OAM-12345-0/TP-2023 ERROR"
    msg.ack.assert_awaited_once()
    proc.metrics_collector.record_fetch_status.assert_called_once_with("failed")
    record_error.assert_called_once_with("browser", "status_too_large")
    record_fetch_result.assert_called_once_with("fetch", "oam", "failed")
