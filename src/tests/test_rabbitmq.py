import pytest
from unittest.mock import AsyncMock, patch

from conftest import make_rabbit, make_incoming_message, OAM_BASE_MSG, ZOV_BASE_MSG


# ---------------------------------------------------------------------------
# Unique ID generation
# ---------------------------------------------------------------------------


def test_rabbit_generate_unique_id_deterministic():
    rabbit = make_rabbit()
    msg = {**OAM_BASE_MSG}
    assert rabbit.generate_unique_id(msg) == rabbit.generate_unique_id(msg)


def test_rabbit_generate_unique_id_different():
    rabbit = make_rabbit()
    msg_a = {**OAM_BASE_MSG}
    msg_b = {**OAM_BASE_MSG, "number": "99999"}
    assert rabbit.generate_unique_id(msg_a) != rabbit.generate_unique_id(msg_b)


def test_rabbit_generate_unique_id_zov():
    """ZOV messages produce valid unique IDs"""
    rabbit = make_rabbit()
    uid = rabbit.generate_unique_id(ZOV_BASE_MSG)
    assert isinstance(uid, str) and len(uid) == 32


# ---------------------------------------------------------------------------
# is_resolved
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status_text, expected",
    [
        ("Vaše žádost bylo <b>povoleno</b>", True),
        ("rizeni-povoleno", True),
        ("bylo <b>nepovoleno</b>", True),
        ("was <b>rejected</b>", True),
        ("have been closed", True),
        ("is still being processed", False),
        ("reference number not found", False),
        ("preliminarily assessed positively", True),
        ("has been suspended", False),
    ],
)
def test_rabbit_is_resolved(status_text, expected):
    rabbit = make_rabbit()
    assert rabbit.processor._is_resolved(status_text) is expected


# ---------------------------------------------------------------------------
# Error messages
# ---------------------------------------------------------------------------


def test_rabbit_generate_error_message():
    rabbit = make_rabbit()
    app_details = {**OAM_BASE_MSG}
    result = rabbit.processor._generate_error_message(app_details, "EN")
    assert "OAM-12345/TP-2023" in result


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


def test_rabbit_dedup_cycle():
    rabbit = make_rabbit()
    uid = "test-uid-123"
    assert rabbit.is_message_published(uid) is False
    rabbit.mark_message_as_published(uid)
    assert rabbit.is_message_published(uid) is True
    rabbit.discard_message_id(uid)
    assert rabbit.is_message_published(uid) is False


def test_rabbit_exposes_fetcher_stats_cache_for_admin_command():
    """The /fetcher_stats command reads the same cache used by FetcherMetricsQueue"""
    rabbit = make_rabbit()
    assert rabbit.fetcher_stats is rabbit.processor.fetcher_stats


@pytest.mark.asyncio
async def test_rabbit_publish_message_dedup():
    """First publish goes through, duplicate is skipped"""
    rabbit = make_rabbit()
    msg = {**OAM_BASE_MSG}

    with patch("bot.rabbitmq.prometheus_metrics.record_published_message") as record_published_message:
        await rabbit.publish_message(msg, routing_key="TestQueue")
        assert rabbit.default_exchange.publish.call_count == 1

        await rabbit.publish_message(msg, routing_key="TestQueue")
        assert rabbit.default_exchange.publish.call_count == 1

    record_published_message.assert_any_call("TestQueue", "published")
    record_published_message.assert_any_call("TestQueue", "duplicate_skipped")


# ---------------------------------------------------------------------------
# on_update_message — early-return paths (no DB write, no enqueue)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rabbit_on_update_status_unchanged_polled_only_updates_last_checked():
    """Plain polled refresh with no change: bump last_updated, do NOT enqueue —
    silent path is the most common one and must stay silent
    """
    rabbit = make_rabbit()
    status_text = "Application 12345 is still being processed"
    rabbit.db.fetch_application_status = AsyncMock(return_value=status_text)

    msg = make_incoming_message({**OAM_BASE_MSG, "status": status_text})
    with patch("bot.rabbitmq.prometheus_metrics.record_rabbitmq_message") as record_rabbitmq_message:
        await rabbit.on_update_message(msg)

    rabbit.db.update_last_checked.assert_called_once_with(100, "12345", "TP", 2023)
    rabbit.db.enqueue_notification.assert_not_called()
    rabbit.db.update_application_status.assert_not_called()
    record_rabbitmq_message.assert_called_once_with("StatusUpdateQueue", "ignored")


@pytest.mark.asyncio
async def test_rabbit_on_update_failed_refresh_drops_silently():
    """Failed refresh: do not rewrite the DB (avoid mass status loss on fetcher
    outage) and do not enqueue
    """
    rabbit = make_rabbit()
    rabbit.db.fetch_application_status = AsyncMock(return_value="old status")

    msg = make_incoming_message({
        **OAM_BASE_MSG,
        "status": "12345 ERROR",
        "failed": True,
        "request_type": "refresh",
    })
    await rabbit.on_update_message(msg)

    rabbit.db.update_application_status.assert_not_called()
    rabbit.db.update_last_checked.assert_not_called()
    rabbit.db.enqueue_notification.assert_not_called()


@pytest.mark.asyncio
async def test_rabbit_on_update_number_mismatch_drops_silently():
    """Number not in fetcher's response text → suspect mis-routing, drop"""
    rabbit = make_rabbit()
    rabbit.db.fetch_application_status = AsyncMock(return_value="old status")

    msg = make_incoming_message({
        **OAM_BASE_MSG,
        "status": "Status for 99999 is being processed",
    })
    await rabbit.on_update_message(msg)

    rabbit.db.update_application_status.assert_not_called()
    rabbit.db.update_last_checked.assert_not_called()
    rabbit.db.enqueue_notification.assert_not_called()


@pytest.mark.asyncio
async def test_rabbit_on_update_failed_fetch_reminder_silently_returns():
    """Failed fetch triggered by a reminder: stay silent — the user did not
    initiate the lookup, so nothing to surface
    """
    rabbit = make_rabbit()
    rabbit.db.fetch_application_status = AsyncMock(return_value="old status")

    msg = make_incoming_message({
        **OAM_BASE_MSG,
        "status": "12345 ERROR",
        "failed": True,
        "request_type": "fetch",
        "is_reminder": True,
    })
    await rabbit.on_update_message(msg)

    rabbit.db.update_application_status.assert_not_called()
    rabbit.db.enqueue_notification.assert_not_called()


# ---------------------------------------------------------------------------
# on_update_message — outbox enqueue paths (one assertion per kind)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rabbit_on_update_status_changed_enqueues_status_change():
    """Status genuinely changed: write to Applications, enqueue a status_change
    row carrying the rendered text and the application_id as origin_ref
    """
    rabbit = make_rabbit()
    old_status = "Application 12345 is still being processed"
    new_status = "Vaše žádost 12345 bylo <b>povoleno</b>"
    rabbit.db.fetch_application_status = AsyncMock(return_value=old_status)
    rabbit.db.update_application_status = AsyncMock(return_value=42)
    rabbit.db.fetch_user_language = AsyncMock(return_value="EN")
    rabbit.db.enqueue_notification = AsyncMock(return_value=999)

    msg = make_incoming_message({**OAM_BASE_MSG, "status": new_status})
    await rabbit.on_update_message(msg)

    rabbit.db.update_application_status.assert_called_once()
    call_args = rabbit.db.update_application_status.call_args[0]
    assert call_args[5] is True  # is_resolved (povoleno is final)

    rabbit.db.enqueue_notification.assert_awaited_once()
    kwargs = rabbit.db.enqueue_notification.call_args.kwargs
    assert kwargs["kind"] == "status_change"
    assert kwargs["origin_ref"] == 42
    assert new_status in kwargs["text"]
    rabbit.notification_dispatcher.wake.assert_called_once()


@pytest.mark.asyncio
async def test_rabbit_on_update_force_refresh_unchanged_enqueues_force_refresh_unchanged():
    """force_refresh + identical status: refresh last_updated and enqueue an
    acknowledgement row so the user always sees a response to /force_refresh
    """
    rabbit = make_rabbit()
    status_text = "Application 12345 is still being processed"
    rabbit.db.fetch_application_status = AsyncMock(return_value=status_text)
    rabbit.db.update_last_checked = AsyncMock(return_value=42)
    rabbit.db.fetch_user_language = AsyncMock(return_value="EN")
    rabbit.db.enqueue_notification = AsyncMock(return_value=999)

    msg = make_incoming_message({
        **OAM_BASE_MSG,
        "status": status_text,
        "force_refresh": True,
    })
    await rabbit.on_update_message(msg)

    rabbit.db.update_last_checked.assert_called_once_with(100, "12345", "TP", 2023)
    rabbit.db.update_application_status.assert_not_called()

    rabbit.db.enqueue_notification.assert_awaited_once()
    kwargs = rabbit.db.enqueue_notification.call_args.kwargs
    assert kwargs["kind"] == "force_refresh_unchanged"
    assert kwargs["origin_ref"] == 42
    rabbit.notification_dispatcher.wake.assert_called_once()


@pytest.mark.asyncio
async def test_rabbit_on_update_failed_fetch_initial_enqueues_failed_fetch():
    """Failed initial fetch (not a reminder): freeze the row, enqueue a
    failed_fetch row carrying the user-facing error text
    """
    rabbit = make_rabbit()
    rabbit.db.fetch_application_status = AsyncMock(return_value="old status")
    rabbit.db.update_application_status = AsyncMock(return_value=42)
    rabbit.db.fetch_user_language = AsyncMock(return_value="EN")
    rabbit.db.enqueue_notification = AsyncMock(return_value=999)

    msg = make_incoming_message({
        **OAM_BASE_MSG,
        "status": "OAM-12345-0/TP-2023 ERROR",
        "failed": True,
        "request_type": "fetch",
        "is_reminder": False,
    })
    await rabbit.on_update_message(msg)

    rabbit.db.update_application_status.assert_called_once()
    call_args = rabbit.db.update_application_status.call_args[0]
    assert call_args[5] is True  # is_resolved on failed initial fetch

    rabbit.db.enqueue_notification.assert_awaited_once()
    kwargs = rabbit.db.enqueue_notification.call_args.kwargs
    assert kwargs["kind"] == "failed_fetch"
    assert kwargs["origin_ref"] == 42
    rabbit.notification_dispatcher.wake.assert_called_once()


@pytest.mark.asyncio
async def test_rabbit_on_update_skips_enqueue_when_update_returns_none():
    """If update_application_status matched no rows (e.g. user unsubscribed
    while the fetch was in flight), do not enqueue a notification
    """
    rabbit = make_rabbit()
    rabbit.db.fetch_application_status = AsyncMock(return_value="old status")
    rabbit.db.update_application_status = AsyncMock(return_value=None)

    msg = make_incoming_message({
        **OAM_BASE_MSG,
        "status": "Vaše žádost 12345 bylo <b>povoleno</b>",
    })
    await rabbit.on_update_message(msg)

    rabbit.db.enqueue_notification.assert_not_called()


# ---------------------------------------------------------------------------
# on_update_message — ZOV-specific resolution semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rabbit_on_update_zov_pre_approved_is_resolved():
    """ZOV pre_approved must be treated as resolved (final positive status for ZOV),
    even without the 'rizeni-povoleno' marker. Same enqueue path as OAM
    """
    rabbit = make_rabbit()
    old_status = "ISTA202504220001 not found"
    new_status = (
        'Číslo žádosti o vízum<strong> ISTA202504220001 </strong>bylo '
        '<b>předběžně vyhodnoceno kladně</b>.'
    )
    rabbit.db.fetch_application_status = AsyncMock(return_value=old_status)
    rabbit.db.update_application_status = AsyncMock(return_value=42)
    rabbit.db.fetch_user_language = AsyncMock(return_value="EN")
    rabbit.db.enqueue_notification = AsyncMock(return_value=999)

    msg = make_incoming_message({**ZOV_BASE_MSG, "status": new_status})
    await rabbit.on_update_message(msg)

    call_args = rabbit.db.update_application_status.call_args[0]
    assert call_args[5] is True, "ZOV pre_approved must be is_resolved=True"
    rabbit.db.enqueue_notification.assert_awaited_once()


# ---------------------------------------------------------------------------
# on_expiration_message — durable via the outbox now
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rabbit_on_expiration_enqueues_expiration_row():
    """Expiration: resolve the application, then enqueue an expiration row.
    No more inline notify_user / mark_user_inactive — the dispatcher handles
    delivery and dead-user verdict centrally
    """
    rabbit = make_rabbit()
    rabbit.db.resolve_application = AsyncMock(return_value=True)
    rabbit.db.fetch_user_language = AsyncMock(return_value="EN")
    rabbit.db.enqueue_notification = AsyncMock(return_value=999)

    msg = make_incoming_message({
        **OAM_BASE_MSG,
        "application_id": 42,
        "request_type": "expire",
    })
    await rabbit.on_expiration_message(msg)

    rabbit.db.resolve_application.assert_called_once_with(42)
    rabbit.db.enqueue_notification.assert_awaited_once()
    kwargs = rabbit.db.enqueue_notification.call_args.kwargs
    assert kwargs["kind"] == "expiration"
    assert kwargs["origin_ref"] == 42
    assert "OAM-12345/TP-2023" in kwargs["text"]
    rabbit.notification_dispatcher.wake.assert_called_once()


@pytest.mark.asyncio
async def test_rabbit_on_expiration_zov_uses_correct_identifier():
    """ZOV expiration carries the ISTA identifier in the rendered text"""
    rabbit = make_rabbit()
    rabbit.db.resolve_application = AsyncMock(return_value=True)
    rabbit.db.fetch_user_language = AsyncMock(return_value="EN")
    rabbit.db.enqueue_notification = AsyncMock(return_value=999)

    msg = make_incoming_message({
        **ZOV_BASE_MSG, "application_id": 99, "request_type": "expire",
    })
    await rabbit.on_expiration_message(msg)

    rabbit.db.resolve_application.assert_called_once_with(99)
    text = rabbit.db.enqueue_notification.call_args.kwargs["text"]
    assert "ISTA202504220001" in text


# ---------------------------------------------------------------------------
# on_service_message — fetcher stats cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rabbit_on_service_updates_fetcher_stats_cache():
    """FetcherMetricsQueue updates the Telegram-facing stats cache"""
    rabbit = make_rabbit()
    payload = {"fetcher_id": "fetcher-1", "connection_status": "OK"}
    msg = make_incoming_message(payload)

    with patch("bot.rabbitmq.prometheus_metrics.record_rabbitmq_message") as record_rabbitmq_message:
        await rabbit.on_service_message(msg)

    rabbit.processor.fetcher_stats.update_fetcher_metrics.assert_awaited_once_with("fetcher-1", payload)
    record_rabbitmq_message.assert_called_once_with("FetcherMetricsQueue", "processed")


@pytest.mark.asyncio
async def test_rabbit_on_service_missing_fetcher_id_records_failure():
    """Fetcher stats without fetcher_id are handled as known bad service messages"""
    rabbit = make_rabbit()
    msg = make_incoming_message({"connection_status": "OK"})

    with patch("bot.processor.prometheus_metrics.record_error") as record_error, \
         patch("bot.rabbitmq.prometheus_metrics.record_rabbitmq_message") as record_rabbitmq_message:
        await rabbit.on_service_message(msg)

    rabbit.processor.fetcher_stats.update_fetcher_metrics.assert_not_called()
    record_error.assert_called_once_with("rabbitmq", "missing_fetcher_id")
    record_rabbitmq_message.assert_called_once_with("FetcherMetricsQueue", "failed")
