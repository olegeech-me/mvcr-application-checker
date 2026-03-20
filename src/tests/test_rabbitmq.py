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
    assert rabbit.is_resolved(status_text) is expected


# ---------------------------------------------------------------------------
# Error messages
# ---------------------------------------------------------------------------


def test_rabbit_generate_error_message():
    rabbit = make_rabbit()
    app_details = {**OAM_BASE_MSG}
    result = rabbit._generate_error_message(app_details, "EN")
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


@pytest.mark.asyncio
async def test_rabbit_publish_message_dedup():
    """First publish goes through, duplicate is skipped"""
    rabbit = make_rabbit()
    msg = {**OAM_BASE_MSG}

    await rabbit.publish_message(msg, routing_key="TestQueue")
    assert rabbit.default_exchange.publish.call_count == 1

    await rabbit.publish_message(msg, routing_key="TestQueue")
    assert rabbit.default_exchange.publish.call_count == 1


# ---------------------------------------------------------------------------
# on_update_message — OAM
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rabbit_on_update_status_unchanged():
    """Status unchanged, not forced: update_last_checked, no notification"""
    rabbit = make_rabbit()
    status_text = "Application 12345 is still being processed"
    rabbit.db.fetch_application_status = AsyncMock(return_value=status_text)

    msg_dict = {**OAM_BASE_MSG, "status": status_text}
    msg = make_incoming_message(msg_dict)

    with patch("bot.rabbitmq.notify_user", new_callable=AsyncMock) as mock_notify:
        await rabbit.on_update_message(msg)
        rabbit.db.update_last_checked.assert_called_once_with(100, "12345", "TP", 2023)
        mock_notify.assert_not_called()
        rabbit.db.update_application_status.assert_not_called()


@pytest.mark.asyncio
async def test_rabbit_on_update_status_changed_approved():
    """Status changed to approved: is_resolved=True, user notified"""
    rabbit = make_rabbit()
    old_status = "Application 12345 is still being processed"
    new_status = "Vaše žádost 12345 bylo <b>povoleno</b>"
    rabbit.db.fetch_application_status = AsyncMock(return_value=old_status)
    rabbit.db.update_application_status = AsyncMock(return_value=True)
    rabbit.db.fetch_user_language = AsyncMock(return_value="EN")

    msg_dict = {**OAM_BASE_MSG, "status": new_status}
    msg = make_incoming_message(msg_dict)

    with patch("bot.rabbitmq.notify_user", new_callable=AsyncMock) as mock_notify:
        await rabbit.on_update_message(msg)
        rabbit.db.update_application_status.assert_called_once()
        call_args = rabbit.db.update_application_status.call_args[0]
        assert call_args[5] is True  # is_resolved
        mock_notify.assert_called_once()


@pytest.mark.asyncio
async def test_rabbit_on_update_failed_refresh():
    """Failed refresh: returns early, no DB update, no notification"""
    rabbit = make_rabbit()
    rabbit.db.fetch_application_status = AsyncMock(return_value="old status")

    msg_dict = {
        **OAM_BASE_MSG,
        "status": "12345 ERROR",
        "failed": True,
        "request_type": "refresh",
    }
    msg = make_incoming_message(msg_dict)

    with patch("bot.rabbitmq.notify_user", new_callable=AsyncMock) as mock_notify:
        await rabbit.on_update_message(msg)
        rabbit.db.update_application_status.assert_not_called()
        mock_notify.assert_not_called()


@pytest.mark.asyncio
async def test_rabbit_on_update_number_mismatch():
    """Number not in status text: returns early"""
    rabbit = make_rabbit()
    rabbit.db.fetch_application_status = AsyncMock(return_value="old status")

    msg_dict = {
        **OAM_BASE_MSG,
        "status": "Status for 99999 is being processed",
    }
    msg = make_incoming_message(msg_dict)

    with patch("bot.rabbitmq.notify_user", new_callable=AsyncMock) as mock_notify:
        await rabbit.on_update_message(msg)
        rabbit.db.update_application_status.assert_not_called()
        rabbit.db.update_last_checked.assert_not_called()
        mock_notify.assert_not_called()


@pytest.mark.asyncio
async def test_rabbit_on_update_failed_fetch_non_reminder():
    """Failed fetch (not reminder): is_resolved=True, error notification sent"""
    rabbit = make_rabbit()
    rabbit.db.fetch_application_status = AsyncMock(return_value="old status")
    rabbit.db.update_application_status = AsyncMock(return_value=True)
    rabbit.db.fetch_user_language = AsyncMock(return_value="EN")

    msg_dict = {
        **OAM_BASE_MSG,
        "status": "OAM-12345-0/TP-2023 ERROR",
        "failed": True,
        "request_type": "fetch",
        "is_reminder": False,
    }
    msg = make_incoming_message(msg_dict)

    with patch("bot.rabbitmq.notify_user", new_callable=AsyncMock) as mock_notify:
        await rabbit.on_update_message(msg)
        rabbit.db.update_application_status.assert_called_once()
        call_args = rabbit.db.update_application_status.call_args[0]
        assert call_args[5] is True  # is_resolved
        mock_notify.assert_called_once()


@pytest.mark.asyncio
async def test_rabbit_on_update_failed_fetch_reminder():
    """Failed fetch triggered by reminder: returns early, no update"""
    rabbit = make_rabbit()
    rabbit.db.fetch_application_status = AsyncMock(return_value="old status")

    msg_dict = {
        **OAM_BASE_MSG,
        "status": "12345 ERROR",
        "failed": True,
        "request_type": "fetch",
        "is_reminder": True,
    }
    msg = make_incoming_message(msg_dict)

    with patch("bot.rabbitmq.notify_user", new_callable=AsyncMock) as mock_notify:
        await rabbit.on_update_message(msg)
        rabbit.db.update_application_status.assert_not_called()
        mock_notify.assert_not_called()


@pytest.mark.asyncio
async def test_rabbit_on_update_pre_approved_is_resolved():
    """pre_approved is always a final/resolved status"""
    rabbit = make_rabbit()
    old_status = "Application 12345 not found"
    new_status = "Application 12345 has been preliminarily assessed positively"
    rabbit.db.fetch_application_status = AsyncMock(return_value=old_status)
    rabbit.db.update_application_status = AsyncMock(return_value=True)
    rabbit.db.fetch_user_language = AsyncMock(return_value="EN")

    msg_dict = {**OAM_BASE_MSG, "status": new_status}
    msg = make_incoming_message(msg_dict)

    with patch("bot.rabbitmq.notify_user", new_callable=AsyncMock) as mock_notify:
        await rabbit.on_update_message(msg)
        rabbit.db.update_application_status.assert_called_once()
        call_args = rabbit.db.update_application_status.call_args[0]
        assert call_args[5] is True, "pre_approved must be is_resolved"
        mock_notify.assert_called_once()


# ---------------------------------------------------------------------------
# on_update_message — ZOV
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rabbit_on_update_zov_status_changed():
    """ZOV status change flows through on_update_message correctly"""
    rabbit = make_rabbit()
    old_status = "Visa application number ISTA202504220001 not found"
    new_status = "Visa application number ISTA202504220001 is still being processed"
    rabbit.db.fetch_application_status = AsyncMock(return_value=old_status)
    rabbit.db.update_application_status = AsyncMock(return_value=True)
    rabbit.db.fetch_user_language = AsyncMock(return_value="EN")

    msg_dict = {**ZOV_BASE_MSG, "status": new_status}
    msg = make_incoming_message(msg_dict)

    with patch("bot.rabbitmq.notify_user", new_callable=AsyncMock) as mock_notify:
        await rabbit.on_update_message(msg)
        rabbit.db.update_application_status.assert_called_once()
        call_args = rabbit.db.update_application_status.call_args[0]
        assert call_args[5] is False  # in_progress is NOT resolved
        mock_notify.assert_called_once()


@pytest.mark.asyncio
async def test_rabbit_on_update_zov_pre_approved_is_resolved():
    """ZOV pre_approved must be treated as resolved (final positive status for ZOV),
    even without 'rizeni-povoleno' link in the response"""
    rabbit = make_rabbit()
    old_status = "ISTA202504220001 not found"
    new_status = (
        'Číslo žádosti o vízum<strong> ISTA202504220001 </strong>bylo '
        '<b>předběžně vyhodnoceno kladně</b>.'
    )
    rabbit.db.fetch_application_status = AsyncMock(return_value=old_status)
    rabbit.db.update_application_status = AsyncMock(return_value=True)
    rabbit.db.fetch_user_language = AsyncMock(return_value="EN")

    msg_dict = {**ZOV_BASE_MSG, "status": new_status}
    msg = make_incoming_message(msg_dict)

    with patch("bot.rabbitmq.notify_user", new_callable=AsyncMock) as mock_notify:
        await rabbit.on_update_message(msg)
        rabbit.db.update_application_status.assert_called_once()
        call_args = rabbit.db.update_application_status.call_args[0]
        assert call_args[5] is True, "ZOV pre_approved must be is_resolved=True"
        mock_notify.assert_called_once()


# ---------------------------------------------------------------------------
# on_expiration_message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rabbit_on_expiration_message():
    """Expiration message: resolve application, notify user"""
    rabbit = make_rabbit()
    rabbit.db.resolve_application = AsyncMock(return_value=True)
    rabbit.db.fetch_user_language = AsyncMock(return_value="EN")

    msg_dict = {
        **OAM_BASE_MSG,
        "application_id": 42,
        "request_type": "expire",
    }
    msg = make_incoming_message(msg_dict)

    with patch("bot.rabbitmq.notify_user", new_callable=AsyncMock) as mock_notify:
        await rabbit.on_expiration_message(msg)
        rabbit.db.resolve_application.assert_called_once_with(42)
        mock_notify.assert_called_once()


@pytest.mark.asyncio
async def test_rabbit_on_expiration_zov():
    """ZOV expiration message uses correct identifier in notification"""
    rabbit = make_rabbit()
    rabbit.db.resolve_application = AsyncMock(return_value=True)
    rabbit.db.fetch_user_language = AsyncMock(return_value="EN")

    msg_dict = {**ZOV_BASE_MSG, "application_id": 99, "request_type": "expire"}
    msg = make_incoming_message(msg_dict)

    with patch("bot.rabbitmq.notify_user", new_callable=AsyncMock) as mock_notify:
        await rabbit.on_expiration_message(msg)
        rabbit.db.resolve_application.assert_called_once_with(99)
        notification_text = mock_notify.call_args[0][2]
        assert "ISTA202504220001" in notification_text
