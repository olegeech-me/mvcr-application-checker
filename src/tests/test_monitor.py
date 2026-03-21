import pytest
from datetime import datetime
from unittest.mock import AsyncMock

from bot.monitor import ApplicationMonitor, ReminderMonitor

from conftest import make_oam_db_row, make_zov_db_row


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
