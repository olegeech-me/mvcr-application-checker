import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from conftest import make_oam_db_row, make_zov_db_row

from bot.monitor import (
    ApplicationMonitor,
    NotificationDispatcher,
    ReminderMonitor,
    compute_next_retry_at,
)


def _make_outbox_row(notification_id=1, chat_id=100, kind="status_change",
                     text="hello", attempts=0, origin_ref=42):
    return {
        "id": notification_id,
        "chat_id": chat_id,
        "kind": kind,
        "text": text,
        "attempts": attempts,
        "origin_ref": origin_ref,
    }


# ---------------------------------------------------------------------------
# ApplicationMonitor.check_for_updates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monitor_check_for_updates_message():
    """check_for_updates publishes correct message to RefreshStatusQueue"""
    db = AsyncMock()
    rabbit = AsyncMock()
    db.fetch_applications_needing_update = AsyncMock(return_value=[make_oam_db_row()])

    monitor = ApplicationMonitor(db, rabbit)
    await monitor.check_for_updates()

    rabbit.publish_message.assert_called_once()
    published_msg = rabbit.publish_message.call_args[0][0]
    assert published_msg["chat_id"] == 100
    assert published_msg["number"] == "12345"
    assert published_msg["suffix"] == "0"
    assert published_msg["type"] == "TP"
    assert published_msg["year"] == 2023
    assert published_msg["request_type"] == "refresh"
    assert published_msg["force_refresh"] is False
    assert rabbit.publish_message.call_args[1]["routing_key"] == "RefreshStatusQueue"


@pytest.mark.asyncio
async def test_monitor_check_for_updates_oam_source():
    """check_for_updates includes source='oam' for OAM apps"""
    db = AsyncMock()
    rabbit = AsyncMock()
    db.fetch_applications_needing_update = AsyncMock(return_value=[make_oam_db_row()])

    monitor = ApplicationMonitor(db, rabbit)
    await monitor.check_for_updates()

    published_msg = rabbit.publish_message.call_args[0][0]
    assert published_msg["source"] == "oam"


@pytest.mark.asyncio
async def test_monitor_check_for_updates_zov_source():
    """check_for_updates includes source='zov' in message for ZOV apps"""
    db = AsyncMock()
    rabbit = AsyncMock()
    db.fetch_applications_needing_update = AsyncMock(return_value=[make_zov_db_row()])

    monitor = ApplicationMonitor(db, rabbit)
    await monitor.check_for_updates()

    published_msg = rabbit.publish_message.call_args[0][0]
    assert published_msg["source"] == "zov"
    assert published_msg["number"] == "ISTA202504220001"


# ---------------------------------------------------------------------------
# ApplicationMonitor.expire_stale_not_found_applications
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monitor_expire_stale_message():
    """expire_stale_not_found_applications publishes correct message to ExpirationQueue"""
    db = AsyncMock()
    rabbit = AsyncMock()
    row = make_oam_db_row(
        application_id=42,
        created_at=datetime(2023, 6, 1),
        application_state="NOT_FOUND",
    )
    db.fetch_applications_to_expire = AsyncMock(return_value=[row])

    monitor = ApplicationMonitor(db, rabbit)
    await monitor.expire_stale_not_found_applications()

    rabbit.publish_message.assert_called_once()
    published_msg = rabbit.publish_message.call_args[0][0]
    assert published_msg["application_id"] == 42
    assert published_msg["request_type"] == "expire"
    assert rabbit.publish_message.call_args[1]["routing_key"] == "ExpirationQueue"


@pytest.mark.asyncio
async def test_monitor_expire_stale_zov_source():
    """expire_stale_not_found_applications includes source='zov' for ZOV apps"""
    db = AsyncMock()
    rabbit = AsyncMock()
    row = make_zov_db_row(
        application_id=99,
        created_at=datetime(2025, 1, 1),
        application_state="NOT_FOUND",
    )
    db.fetch_applications_to_expire = AsyncMock(return_value=[row])

    monitor = ApplicationMonitor(db, rabbit)
    await monitor.expire_stale_not_found_applications()

    published_msg = rabbit.publish_message.call_args[0][0]
    assert published_msg["source"] == "zov"


# ---------------------------------------------------------------------------
# ReminderMonitor.trigger_reminders
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reminder_trigger_reminders_message():
    """trigger_reminders publishes correct message to ApplicationFetchQueue"""
    db = AsyncMock()
    rabbit = AsyncMock()
    row = make_oam_db_row(last_updated=datetime(2023, 3, 15))
    db.fetch_due_reminders = AsyncMock(return_value=[row])

    reminder_mon = ReminderMonitor(db, rabbit)
    await reminder_mon.trigger_reminders()

    rabbit.publish_message.assert_called_once()
    published_msg = rabbit.publish_message.call_args[0][0]
    assert published_msg["force_refresh"] is True
    assert published_msg["is_reminder"] is True
    assert published_msg["request_type"] == "fetch"
    assert rabbit.publish_message.call_args[1]["routing_key"] == "ApplicationFetchQueue"


@pytest.mark.asyncio
async def test_reminder_trigger_reminders_zov_source():
    """trigger_reminders includes source='zov' for ZOV apps"""
    db = AsyncMock()
    rabbit = AsyncMock()
    db.fetch_due_reminders = AsyncMock(return_value=[make_zov_db_row()])

    reminder_mon = ReminderMonitor(db, rabbit)
    await reminder_mon.trigger_reminders()

    published_msg = rabbit.publish_message.call_args[0][0]
    assert published_msg["source"] == "zov"


# ---------------------------------------------------------------------------
# compute_next_retry_at — backoff policy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "current_attempts, base, max_, expected_seconds",
    [
        (0, 60, 3600, 60),
        (1, 60, 3600, 120),
        (2, 60, 3600, 240),
        (5, 60, 3600, 1920),
        (10, 60, 3600, 3600),  # capped
        (100, 60, 3600, 3600),  # still capped, no overflow
    ],
)
def test_compute_next_retry_at_caps_exponential_growth(current_attempts, base, max_, expected_seconds):
    """Backoff doubles per attempt but is bounded by max_interval — guarantees
    worst-case recovery latency after extended outages
    """
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    result = compute_next_retry_at(current_attempts, base, max_, now=now)
    expected = (now + timedelta(seconds=expected_seconds)).replace(tzinfo=None)
    assert result == expected


def test_compute_next_retry_at_returns_naive_timestamp_for_pg_timestamp_column():
    """Notifications.next_attempt_at is TIMESTAMP WITHOUT TIME ZONE; asyncpg
    rejects tz-aware values when encoding $2 for bump_attempt
    """
    result = compute_next_retry_at(3, base_interval=300, max_interval=3600)
    assert result.tzinfo is None


# ---------------------------------------------------------------------------
# NotificationDispatcher.deliver_pending — verdict routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatcher_no_due_rows_is_a_noop():
    """Empty pool short-circuits — no notify_user calls, no finalizer calls"""
    db = AsyncMock()
    bot = AsyncMock()
    db.claim_due_notifications = AsyncMock(return_value=[])

    disp = NotificationDispatcher(db, bot)
    with patch("bot.monitor.notify_user", new_callable=AsyncMock) as mock_notify:
        await disp.deliver_pending()

    mock_notify.assert_not_called()
    db.mark_delivered.assert_not_called()
    db.bump_attempt.assert_not_called()
    db.mark_user_inactive.assert_not_called()


@pytest.mark.asyncio
async def test_dispatcher_ok_verdict_marks_delivered():
    db = AsyncMock()
    bot = AsyncMock()
    db.claim_due_notifications = AsyncMock(return_value=[_make_outbox_row(notification_id=7)])

    disp = NotificationDispatcher(db, bot)
    with patch("bot.monitor.notify_user", new_callable=AsyncMock, return_value="ok"), \
         patch("bot.monitor.prometheus_metrics.record_notification") as record_notification:
        await disp.deliver_pending()

    db.mark_delivered.assert_awaited_once_with(7)
    db.bump_attempt.assert_not_called()
    db.mark_user_inactive.assert_not_called()
    record_notification.assert_called_once_with("status_change", "ok")


@pytest.mark.asyncio
async def test_dispatcher_dead_user_verdict_deactivates_and_leaves_row_pending():
    """dead_user must NOT mark_delivered — the row stays pending so a future
    reactivation re-exposes the backlog. is_active=FALSE shields it meanwhile
    """
    db = AsyncMock()
    bot = AsyncMock()
    db.claim_due_notifications = AsyncMock(
        return_value=[_make_outbox_row(notification_id=7, chat_id=100)]
    )

    disp = NotificationDispatcher(db, bot)
    with patch("bot.monitor.notify_user", new_callable=AsyncMock, return_value="dead_user"):
        await disp.deliver_pending()

    db.mark_user_inactive.assert_awaited_once()
    assert db.mark_user_inactive.call_args.args[0] == 100
    db.mark_delivered.assert_not_called()
    db.bump_attempt.assert_not_called()


@pytest.mark.asyncio
async def test_dispatcher_retryable_gave_up_verdict_bumps_with_capped_backoff():
    """Retryable exhaust must call bump_attempt with a future timestamp produced
    by compute_next_retry_at (caller-computed, not SQL-computed)
    """
    db = AsyncMock()
    bot = AsyncMock()
    db.claim_due_notifications = AsyncMock(
        return_value=[_make_outbox_row(notification_id=7, attempts=3)]
    )

    disp = NotificationDispatcher(db, bot)
    with patch("bot.monitor.notify_user", new_callable=AsyncMock, return_value="retryable_gave_up"):
        await disp.deliver_pending()

    db.bump_attempt.assert_awaited_once()
    args, kwargs = db.bump_attempt.call_args
    assert args[0] == 7
    assert isinstance(args[1], datetime)
    assert args[1].tzinfo is None
    assert args[1] > datetime.utcnow()
    assert kwargs.get("last_error") == "retryable_gave_up"
    db.mark_delivered.assert_not_called()
    db.mark_user_inactive.assert_not_called()


@pytest.mark.asyncio
async def test_dispatcher_permanent_other_verdict_marks_delivered_with_error():
    """permanent_other ends the row's life so we don't retry-loop on a
    structurally bad payload, but records the error for postmortem
    """
    db = AsyncMock()
    bot = AsyncMock()
    db.claim_due_notifications = AsyncMock(return_value=[_make_outbox_row(notification_id=7)])

    disp = NotificationDispatcher(db, bot)
    with patch("bot.monitor.notify_user", new_callable=AsyncMock, return_value="permanent_other"):
        await disp.deliver_pending()

    db.mark_delivered.assert_awaited_once()
    args, kwargs = db.mark_delivered.call_args
    assert args[0] == 7
    assert kwargs.get("last_error") == "permanent_other"
    db.bump_attempt.assert_not_called()
    db.mark_user_inactive.assert_not_called()


# ---------------------------------------------------------------------------
# NotificationDispatcher.wake — happy-path latency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatcher_wake_short_circuits_the_tick():
    """wake() must release _wait_for_work immediately so happy-path delivery
    latency is sub-second instead of bounded by NOTIFY_MONITOR_TICK
    """
    db = AsyncMock()
    bot = AsyncMock()
    disp = NotificationDispatcher(db, bot)

    waiter = asyncio.create_task(disp._wait_for_work())
    await asyncio.sleep(0)  # let the waiter park on its events
    disp.wake()
    await asyncio.wait_for(waiter, timeout=1.0)
    assert disp.wakeup_event.is_set()


@pytest.mark.asyncio
async def test_dispatcher_shutdown_releases_wait():
    """shutdown_event must also release _wait_for_work so stop() doesn't hang"""
    db = AsyncMock()
    bot = AsyncMock()
    disp = NotificationDispatcher(db, bot)

    waiter = asyncio.create_task(disp._wait_for_work())
    await asyncio.sleep(0)
    disp.stop()
    await asyncio.wait_for(waiter, timeout=1.0)


# ---------------------------------------------------------------------------
# NotificationDispatcher.start — purge runs each tick
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatcher_purge_runs_after_each_deliver_pending():
    """Outbox cleanup is inlined into the dispatcher loop so the table can
    never grow unboundedly. Drive one iteration by stopping after the first
    pass and assert both deliver + purge happened, in that order
    """
    db = AsyncMock()
    bot = AsyncMock()
    db.claim_due_notifications = AsyncMock(return_value=[])

    disp = NotificationDispatcher(db, bot)

    async def stop_after_first_pass(*args, **kwargs):
        disp.stop()
        return 0

    db.purge_old_notifications = AsyncMock(side_effect=stop_after_first_pass)

    await asyncio.wait_for(disp.start(), timeout=1.0)

    db.claim_due_notifications.assert_awaited()
    db.purge_old_notifications.assert_awaited_once()
    args = db.purge_old_notifications.call_args.args
    # (delivered_retention_days, pending_max_age_days) — defaults from config
    assert args == (1, 30)
