import pytest
import pytest_mock
import os
import json
from unittest.mock import Mock, AsyncMock, patch
import asyncio
from bot.rabbitmq import RabbitMQ

os.environ["RUN_MODE"] = "TEST"
os.environ["ADMIN_CHAT_IDS"] = "1234567, 56745679"

from bot.handlers import (
    _parse_application_number_full,
    _parse_application_number,
    _get_user_language,
    _is_admin,
    user_info,
    _show_app_number_final_confirmation,
    check_and_update_limit,
    create_request,
    _is_button_click_abused,
    BUTTON_WAIT_SECONDS,
    start_command,
    subscribe_command,
    enforce_rate_limit,
    set_language_startup,
)
from bot.utils import generate_oam_full_string, categorize_application_status, MVCR_STATUSES
from bot.database import Database

@patch("bot.handlers.ALLOWED_TYPES", new=["MK", "DO", "TP"])
@patch("bot.handlers.get_allowed_years", return_value=[2020, 2021, 2022, 2023, 2042])
@pytest.mark.parametrize(
    "num_str, app_num, app_suffix, app_type, app_year",
    [
        ("OAM-4242/TP-2042", "4242", "0", "TP", "2042"),
        ("12345/TP-2023", "12345", "0", "TP", "2023"),
        ("4242-5/DO-2020", "4242", "5", "DO", "2020"),
        ("oAM-12345-9/MK-2023", "12345", "9", "MK", "2023"),
        ("BAD-NUMBER/MK-2023", None, None, None, None),
        ("oam-4242-6/MK-1999", None, None, None, None),
        ("oam-4242-6/NT-2021", None, None, None, None),
        ("OAM-00004-1/MK-2020", "4", "1", "MK", "2020"),
        ("OAM-00004-99/MK-2020", "4", "99", "MK", "2020"),
        # suffix too long
        ("oAM-12345-911/MK-2023", None, None, None, None),
    ],
)
def test__parse_application_number_full(mock_get_allowed_years, num_str, app_num, app_suffix, app_type, app_year):
    res = _parse_application_number_full(num_str)
    if res:
        assert res == (app_num, app_suffix, app_type, app_year)
    else:
        assert res is None


@pytest.mark.parametrize(
    "num_str, app_num, app_suffix",
    [
        ("OAM-4242/TP-2042", "4242", "0"),
        ("12345/TP-2023", "12345", "0"),
        ("4242-5/DO-2020", "4242", "5"),
        ("oAM-12345-9/MK-2023", "12345", "9"),
        ("BAD-NUMBER/MK-2023", None, None),
        ("OAM-00004-1", "4", "1"),
        ("OAM-00004-99", "4", "99"),
        # suffix too long
        ("OAM-00004-108", None, None),
    ],
)
def test__parse_application_number(num_str, app_num, app_suffix):
    res = _parse_application_number(num_str)
    if res:
        assert res == (app_num, app_suffix)
    else:
        assert res is None


@pytest.mark.parametrize(
    "user_lang_db, user_lang_context, expected_lang",
    [
        (None, None, "EN"),  # both DB and context return no value
        ("RU", None, "RU"),  # DB has a value, context does not
        (None, "CZ", "CZ"),  # context has a value, DB does not
        ("RU", "CZ", "CZ"),  # context value should have precedence over DB
    ],
)
def test_get_user_language(user_lang_db, user_lang_context, expected_lang):
    db_mock = Mock()
    db_mock.fetch_user_language = AsyncMock(return_value=user_lang_db)

    with patch("bot.handlers.db", db_mock):  # Patch the global db instance
        update = Mock()
        update.effective_chat.id = 123456789

        context = Mock()
        context.user_data = {}
        if user_lang_context:
            context.user_data["lang"] = user_lang_context

        lang = asyncio.run(_get_user_language(update, context))
        assert lang == expected_lang
        assert context.user_data["lang"] == expected_lang


# @pytest.mark.asyncio
# async def test_set_language_startup():
#    update = Mock()
#    update.callback_query = AsyncMock()
#    context = Mock()
#    context.user_data = {}
#    mock_db = AsyncMock()
#    mock_db.check_subscription_in_db.return_value = True
#
#    update.callback_query.data = "set_lang_EN"
#    with patch("bot.handlers.db", mock_db):
#        await set_language_startup(update, context)
#        assert context.user_data["lang"] == "EN"


def test_is_admin():
    assert _is_admin("1234567") is True
    assert _is_admin("56745679") is True
    assert _is_admin("123456789") is False


def test_user_info():
    update = Mock()
    update.effective_chat.id = 12345
    update.effective_chat.username = "testuser"
    update.effective_chat.first_name = "Vasya"
    update.effective_chat.last_name = "Pupkin"

    result = user_info(update)
    assert result == "chat_id: 12345, username: testuser, first_name: Vasya, last_name: Pupkin"


def test_check_and_update_limit():
    user_data = {}
    # Assume 5 as the limit. The 6th time it should return False.
    for _attempt in range(5):
        result = check_and_update_limit(user_data, "test_command")
        assert result is True

    result = check_and_update_limit(user_data, "test_command")
    assert result is False


def test_create_request():
    app_data = {
        "number": "4242",
        "suffix": "0",
        "type": "TP",
        "year": "2042",
    }
    chat_id = 123456789
    result = create_request(chat_id, app_data, True)
    assert result["chat_id"] == chat_id
    assert result["number"] == "4242"
    assert result["type"] == "TP"
    assert result["year"] == "2042"
    assert result["force_refresh"] is True


@pytest.mark.asyncio
async def test__show_app_number_final_confirmation():
    update = Mock()
    update.callback_query = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    context = Mock()
    context.user_data = {
        "application_number": "4242",
        "application_suffix": "0",
        "application_type": "TP",
        "application_year": "2042",
    }

    with patch("bot.handlers._get_user_language", return_value="EN"):
        await _show_app_number_final_confirmation(update, context)
        assert update.callback_query.edit_message_text.called


@pytest.mark.asyncio
async def test__is_button_click_abused():
    # Setup a mocked update and context
    update = Mock()
    update.callback_query = AsyncMock()
    context = Mock()
    context.user_data = {}

    # Should not be considered "abuse" on first click
    is_abuse = await _is_button_click_abused(update, context)
    assert not is_abuse

    # Immediate subsequent click should be considered "abuse"
    is_abuse = await _is_button_click_abused(update, context)
    assert is_abuse

    # Sleep for duration slightly more than BUTTON_WAIT_SECONDS and try again
    await asyncio.sleep(BUTTON_WAIT_SECONDS + 0.1)
    is_abuse = await _is_button_click_abused(update, context)
    assert not is_abuse


# @pytest.mark.asyncio
# async def test_start_command():
#    # Mock update and context
#    update = Mock()
#    update.message = AsyncMock()
#    context = Mock()
#
#    with patch("bot.handlers._get_user_language", return_value="EN"):
#        await start_command(update, context)
#        assert update.message.reply_text.called


# @pytest.mark.asyncio
# async def test_subscribe_command_already_subscribed():
#    # Mock update and context
#    update = Mock()
#    update.message = AsyncMock()
#    context = Mock()
#    context.args = []
#    mock_db = AsyncMock()
#    mock_db.check_subscription_in_db.return_value = True
#
#    with patch("bot.handlers.db", mock_db), patch("bot.handlers._get_user_language", return_value="EN"):
#        await subscribe_command(update, context)
#        update.message.reply_text.assert_called_with("You are already subscribed.")


@pytest.mark.parametrize(
    "app_details, expected",
    [
        # OAM with short keys (RabbitMQ message format)
        ({"number": "4242", "suffix": "0", "type": "TP", "year": "2042"},
         "OAM-4242/TP-2042"),
        # OAM with suffix
        ({"number": "4242", "suffix": "5", "type": "DO", "year": "2020"},
         "OAM-4242-5/DO-2020"),
        # OAM with DB column keys
        ({"application_number": "12345", "application_suffix": "0",
          "application_type": "MK", "application_year": "2023"},
         "OAM-12345/MK-2023"),
        # OAM with explicit source="oam"
        ({"number": "100", "suffix": "0", "type": "TP", "year": "2024",
          "source": "oam"},
         "OAM-100/TP-2024"),
        # ZOV with short key
        ({"number": "ISTA202504220001", "source": "zov"},
         "ISTA202504220001"),
        # ZOV with DB column keys
        ({"application_number": "ISTA202601150003",
          "application_source": "zov", "application_type": "ZOV",
          "application_year": 0, "application_suffix": "0"},
         "ISTA202601150003"),
        # No source key at all defaults to OAM
        ({"number": "999", "suffix": "0", "type": "TP", "year": "2025"},
         "OAM-999/TP-2025"),
    ],
)
def test_generate_oam_full_string(app_details, expected):
    assert generate_oam_full_string(app_details) == expected


@pytest.mark.parametrize(
    "status_text, expected_category, expected_emoji",
    [
        ("Your application has been preliminarily assessed positively", "pre_approved", "⭐"),
        ("Vaše žádost bylo předběžně vyhodnoceno kladně", "pre_approved", "⭐"),
        ("Your application bylo <b>povoleno</b>", "approved", "🟢"),
        ("rizeni-povoleno", "approved", "🟢"),
        ("is still being processed", "in_progress", "🟡"),
        ("was <b>rejected</b>", "denied", "🔴"),
        ("reference number not found", "not_found", "⚪️"),
        ("has been suspended", "suspended", "🟠"),
        ("totally unknown status xyz", None, None),
    ],
)
def test_categorize_application_status(status_text, expected_category, expected_emoji):
    category, emoji = categorize_application_status(status_text)
    assert category == expected_category
    assert emoji == expected_emoji


def test_pre_approved_not_in_resolved_statuses():
    """pre_approved must NOT be treated as a final/resolved status"""
    final_keywords = MVCR_STATUSES.get("approved")[0] + MVCR_STATUSES.get("denied")[0]
    pre_approved_keywords = MVCR_STATUSES.get("pre_approved")[0]
    for kw in pre_approved_keywords:
        assert kw not in final_keywords, f"'{kw}' should not be in resolved statuses"


def test_enforce_rate_limit():
    update = Mock()
    update.effective_chat.id = "123456789"
    update.message = AsyncMock()
    update.message.reply_text = AsyncMock()
    update.callback_query = AsyncMock()
    update.callback_query.message = AsyncMock()
    update.callback_query.message.reply_text = AsyncMock()
    context = Mock()
    context.user_data = {}

    for _attempt in range(5):
        result = asyncio.run(enforce_rate_limit(update, context, "test_command"))
        assert result

    # Testing rate limit for the 6th time, should return False
    result = asyncio.run(enforce_rate_limit(update, context, "test_command"))
    assert not result


# ---------------------------------------------------------------------------
# Database class unit tests (mocked asyncpg pool)
# ---------------------------------------------------------------------------

class _FakeAcquire:
    """Async context manager that yields a mock connection"""
    def __init__(self, conn):
        self._conn = conn
    async def __aenter__(self):
        return self._conn
    async def __aexit__(self, *exc):
        pass


def _make_db_with_mock_pool():
    """Create a Database instance with an injected mock pool + connection"""
    db = Database("testdb", "user", "pass", "localhost", 5432, None)
    conn = AsyncMock()
    pool = Mock()
    pool.acquire = Mock(return_value=_FakeAcquire(conn))
    db.pool = pool
    return db, conn


@pytest.mark.asyncio
async def test_db_insert_application_oam_default_source():
    """insert_application without explicit source defaults to 'oam'"""
    db, conn = _make_db_with_mock_pool()
    result = await db.insert_application(
        chat_id=100, application_number="4242",
        application_suffix="0", application_type="TP", application_year=2042,
    )
    assert result is True
    query_arg = conn.execute.call_args[0][0]
    assert "application_source" in query_arg
    params = conn.execute.call_args[0][1:]
    assert params[-1] == "oam"


@pytest.mark.asyncio
async def test_db_insert_application_zov_source():
    """insert_application with application_source='zov'"""
    db, conn = _make_db_with_mock_pool()
    result = await db.insert_application(
        chat_id=100, application_number="ISTA202504220001",
        application_suffix="0", application_type="ZOV", application_year=0,
        application_source="zov",
    )
    assert result is True
    params = conn.execute.call_args[0][1:]
    assert params[-1] == "zov"


@pytest.mark.asyncio
async def test_db_insert_application_duplicate():
    """insert_application returns False on UniqueViolationError"""
    import asyncpg
    db, conn = _make_db_with_mock_pool()
    conn.execute.side_effect = asyncpg.UniqueViolationError("")
    result = await db.insert_application(
        chat_id=100, application_number="4242",
        application_suffix="0", application_type="TP", application_year=2042,
    )
    assert result is False


@pytest.mark.asyncio
async def test_db_fetch_applications_needing_update_has_source():
    """fetch_applications_needing_update SELECT includes application_source"""
    from datetime import timedelta
    db, conn = _make_db_with_mock_pool()
    conn.fetch.return_value = []
    await db.fetch_applications_needing_update(timedelta(hours=1), timedelta(hours=6))
    query = conn.fetch.call_args[0][0]
    assert "application_source" in query


@pytest.mark.asyncio
async def test_db_fetch_applications_to_expire_has_source():
    """fetch_applications_to_expire SELECT includes application_source"""
    from datetime import timedelta
    db, conn = _make_db_with_mock_pool()
    conn.fetch.return_value = []
    await db.fetch_applications_to_expire(timedelta(days=30))
    query = conn.fetch.call_args[0][0]
    assert "application_source" in query


@pytest.mark.asyncio
async def test_db_fetch_due_reminders_has_source():
    """fetch_due_reminders SELECT includes application_source"""
    db, conn = _make_db_with_mock_pool()
    conn.fetch.return_value = []
    await db.fetch_due_reminders()
    query = conn.fetch.call_args[0][0]
    assert "application_source" in query


@pytest.mark.asyncio
async def test_db_fetch_user_reminders_has_source():
    """fetch_user_reminders SELECT includes application_source"""
    db, conn = _make_db_with_mock_pool()
    conn.fetch.return_value = []
    await db.fetch_user_reminders(chat_id=100)
    query = conn.fetch.call_args[0][0]
    assert "application_source" in query


@pytest.mark.asyncio
async def test_db_fetch_user_subscriptions_returns_source():
    """fetch_user_subscriptions (SELECT *) returns dicts that include application_source"""
    db, conn = _make_db_with_mock_pool()
    fake_row = {
        "application_id": 1, "user_id": 1, "application_number": "4242",
        "application_suffix": "0", "application_type": "TP", "application_year": 2042,
        "current_status": "Unknown", "application_state": "UNKNOWN",
        "is_resolved": False, "application_source": "oam",
    }
    conn.fetch.return_value = [fake_row]
    rows = await db.fetch_user_subscriptions(chat_id=100)
    assert len(rows) == 1
    assert rows[0]["application_source"] == "oam"


# ---------------------------------------------------------------------------
# RabbitMQ class unit tests (mocked dependencies)
# ---------------------------------------------------------------------------

def _make_rabbit():
    """Create a RabbitMQ instance with mocked bot, db, metrics, and exchange"""
    bot = Mock()
    db = AsyncMock()
    metrics = Mock()
    rabbit = RabbitMQ("host", "user", "pass", bot, db, 300, metrics, None)
    rabbit.default_exchange = AsyncMock()
    return rabbit


def _make_incoming_message(msg_dict, headers=None):
    """Create a mock aio_pika.IncomingMessage with async context manager"""
    msg = Mock()
    msg.body = json.dumps(msg_dict).encode("utf-8")
    msg.headers = headers or {}
    msg.process = Mock(return_value=AsyncMock())
    return msg


_OAM_BASE_MSG = {
    "chat_id": 100,
    "number": "12345",
    "suffix": "0",
    "type": "TP",
    "year": 2023,
    "force_refresh": False,
    "failed": False,
    "request_type": "refresh",
    "last_updated": "2023-01-01T00:00:00",
}


def test_rabbit_generate_unique_id_deterministic():
    rabbit = _make_rabbit()
    msg = {**_OAM_BASE_MSG}
    assert rabbit.generate_unique_id(msg) == rabbit.generate_unique_id(msg)


def test_rabbit_generate_unique_id_different():
    rabbit = _make_rabbit()
    msg_a = {**_OAM_BASE_MSG}
    msg_b = {**_OAM_BASE_MSG, "number": "99999"}
    assert rabbit.generate_unique_id(msg_a) != rabbit.generate_unique_id(msg_b)


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
        ("preliminarily assessed positively", False),
        ("has been suspended", False),
    ],
)
def test_rabbit_is_resolved(status_text, expected):
    rabbit = _make_rabbit()
    assert rabbit.is_resolved(status_text) is expected


def test_rabbit_generate_error_message():
    rabbit = _make_rabbit()
    app_details = {**_OAM_BASE_MSG}
    result = rabbit._generate_error_message(app_details, "EN")
    assert "OAM-12345/TP-2023" in result


def test_rabbit_dedup_cycle():
    rabbit = _make_rabbit()
    uid = "test-uid-123"
    assert rabbit.is_message_published(uid) is False
    rabbit.mark_message_as_published(uid)
    assert rabbit.is_message_published(uid) is True
    rabbit.discard_message_id(uid)
    assert rabbit.is_message_published(uid) is False


@pytest.mark.asyncio
async def test_rabbit_on_update_status_unchanged():
    """Status unchanged, not forced: update_last_checked, no notification"""
    rabbit = _make_rabbit()
    status_text = "Application 12345 is still being processed"
    rabbit.db.fetch_application_status = AsyncMock(return_value=status_text)

    msg_dict = {**_OAM_BASE_MSG, "status": status_text}
    msg = _make_incoming_message(msg_dict)

    with patch("bot.rabbitmq.notify_user", new_callable=AsyncMock) as mock_notify:
        await rabbit.on_update_message(msg)
        rabbit.db.update_last_checked.assert_called_once_with(100, "12345", "TP", 2023)
        mock_notify.assert_not_called()
        rabbit.db.update_application_status.assert_not_called()


@pytest.mark.asyncio
async def test_rabbit_on_update_status_changed_approved():
    """Status changed to approved: is_resolved=True, user notified"""
    rabbit = _make_rabbit()
    old_status = "Application 12345 is still being processed"
    new_status = "Vaše žádost 12345 bylo <b>povoleno</b>"
    rabbit.db.fetch_application_status = AsyncMock(return_value=old_status)
    rabbit.db.update_application_status = AsyncMock(return_value=True)
    rabbit.db.fetch_user_language = AsyncMock(return_value="EN")

    msg_dict = {**_OAM_BASE_MSG, "status": new_status}
    msg = _make_incoming_message(msg_dict)

    with patch("bot.rabbitmq.notify_user", new_callable=AsyncMock) as mock_notify:
        await rabbit.on_update_message(msg)
        rabbit.db.update_application_status.assert_called_once()
        call_args = rabbit.db.update_application_status.call_args[0]
        assert call_args[5] is True  # is_resolved
        mock_notify.assert_called_once()


@pytest.mark.asyncio
async def test_rabbit_on_update_failed_refresh():
    """Failed refresh: returns early, no DB update, no notification"""
    rabbit = _make_rabbit()
    rabbit.db.fetch_application_status = AsyncMock(return_value="old status")

    msg_dict = {
        **_OAM_BASE_MSG,
        "status": "12345 ERROR",
        "failed": True,
        "request_type": "refresh",
    }
    msg = _make_incoming_message(msg_dict)

    with patch("bot.rabbitmq.notify_user", new_callable=AsyncMock) as mock_notify:
        await rabbit.on_update_message(msg)
        rabbit.db.update_application_status.assert_not_called()
        mock_notify.assert_not_called()


@pytest.mark.asyncio
async def test_rabbit_on_update_number_mismatch():
    """Number not in status text: returns early"""
    rabbit = _make_rabbit()
    rabbit.db.fetch_application_status = AsyncMock(return_value="old status")

    msg_dict = {
        **_OAM_BASE_MSG,
        "status": "Status for 99999 is being processed",
    }
    msg = _make_incoming_message(msg_dict)

    with patch("bot.rabbitmq.notify_user", new_callable=AsyncMock) as mock_notify:
        await rabbit.on_update_message(msg)
        rabbit.db.update_application_status.assert_not_called()
        rabbit.db.update_last_checked.assert_not_called()
        mock_notify.assert_not_called()


@pytest.mark.asyncio
async def test_rabbit_on_update_failed_fetch_non_reminder():
    """Failed fetch (not reminder): is_resolved=True, error notification sent"""
    rabbit = _make_rabbit()
    rabbit.db.fetch_application_status = AsyncMock(return_value="old status")
    rabbit.db.update_application_status = AsyncMock(return_value=True)
    rabbit.db.fetch_user_language = AsyncMock(return_value="EN")

    msg_dict = {
        **_OAM_BASE_MSG,
        "status": "OAM-12345-0/TP-2023 ERROR",
        "failed": True,
        "request_type": "fetch",
        "is_reminder": False,
    }
    msg = _make_incoming_message(msg_dict)

    with patch("bot.rabbitmq.notify_user", new_callable=AsyncMock) as mock_notify:
        await rabbit.on_update_message(msg)
        rabbit.db.update_application_status.assert_called_once()
        call_args = rabbit.db.update_application_status.call_args[0]
        assert call_args[5] is True  # is_resolved
        mock_notify.assert_called_once()


@pytest.mark.asyncio
async def test_rabbit_on_update_failed_fetch_reminder():
    """Failed fetch triggered by reminder: returns early, no update"""
    rabbit = _make_rabbit()
    rabbit.db.fetch_application_status = AsyncMock(return_value="old status")

    msg_dict = {
        **_OAM_BASE_MSG,
        "status": "12345 ERROR",
        "failed": True,
        "request_type": "fetch",
        "is_reminder": True,
    }
    msg = _make_incoming_message(msg_dict)

    with patch("bot.rabbitmq.notify_user", new_callable=AsyncMock) as mock_notify:
        await rabbit.on_update_message(msg)
        rabbit.db.update_application_status.assert_not_called()
        mock_notify.assert_not_called()


@pytest.mark.asyncio
async def test_rabbit_on_expiration_message():
    """Expiration message: resolve application, notify user"""
    rabbit = _make_rabbit()
    rabbit.db.resolve_application = AsyncMock(return_value=True)
    rabbit.db.fetch_user_language = AsyncMock(return_value="EN")

    msg_dict = {
        **_OAM_BASE_MSG,
        "application_id": 42,
        "request_type": "expire",
    }
    msg = _make_incoming_message(msg_dict)

    with patch("bot.rabbitmq.notify_user", new_callable=AsyncMock) as mock_notify:
        await rabbit.on_expiration_message(msg)
        rabbit.db.resolve_application.assert_called_once_with(42)
        mock_notify.assert_called_once()


@pytest.mark.asyncio
async def test_rabbit_publish_message_dedup():
    """First publish goes through, duplicate is skipped"""
    rabbit = _make_rabbit()
    msg = {**_OAM_BASE_MSG}

    await rabbit.publish_message(msg, routing_key="TestQueue")
    assert rabbit.default_exchange.publish.call_count == 1

    await rabbit.publish_message(msg, routing_key="TestQueue")
    assert rabbit.default_exchange.publish.call_count == 1


# ---------------------------------------------------------------------------
# ApplicationMonitor / ReminderMonitor unit tests
# ---------------------------------------------------------------------------

from bot.monitor import ApplicationMonitor, ReminderMonitor


def _make_oam_db_row(**overrides):
    """Build a fake DB row dict that looks like what the monitor queries return"""
    from datetime import datetime
    row = {
        "chat_id": 100,
        "application_number": "12345",
        "application_suffix": "0",
        "application_type": "TP",
        "application_year": 2023,
        "last_updated": datetime(2023, 1, 1),
        "application_state": "IN_PROGRESS",
        "application_source": "oam",
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_monitor_check_for_updates_message():
    """check_for_updates publishes correct message to RefreshStatusQueue"""
    db = AsyncMock()
    rabbit = AsyncMock()
    db.fetch_applications_needing_update = AsyncMock(return_value=[_make_oam_db_row()])

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
async def test_monitor_expire_stale_message():
    """expire_stale_not_found_applications publishes correct message to ExpirationQueue"""
    from datetime import datetime
    db = AsyncMock()
    rabbit = AsyncMock()
    row = _make_oam_db_row(
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
async def test_reminder_trigger_reminders_message():
    """trigger_reminders publishes correct message to ApplicationFetchQueue"""
    from datetime import datetime
    db = AsyncMock()
    rabbit = AsyncMock()
    row = _make_oam_db_row(last_updated=datetime(2023, 3, 15))
    db.fetch_due_reminders = AsyncMock(return_value=[row])

    reminder_mon = ReminderMonitor(db, rabbit)
    await reminder_mon.trigger_reminders()

    rabbit.publish_message.assert_called_once()
    published_msg = rabbit.publish_message.call_args[0][0]
    assert published_msg["force_refresh"] is True
    assert published_msg["is_reminder"] is True
    assert published_msg["request_type"] == "fetch"
    assert rabbit.publish_message.call_args[1]["routing_key"] == "ApplicationFetchQueue"


# ---------------------------------------------------------------------------
# ApplicationProcessor unit tests (fetcher side)
# ---------------------------------------------------------------------------

from fetcher.application_processor import ApplicationProcessor


def _make_processor():
    """Create an ApplicationProcessor with mocked dependencies"""
    messaging = AsyncMock()
    browser = AsyncMock()
    metrics = Mock()
    metrics.increment_request_state = Mock()
    metrics.decrement_request_state = Mock()
    return ApplicationProcessor(messaging, browser, metrics, "http://test.url")


def test_processor_generate_error_message_oam():
    proc = _make_processor()
    app_details = {"number": "12345", "suffix": "0", "type": "TP", "year": "2023"}
    result = proc._generate_error_message(app_details)
    assert result == "OAM-12345-0/TP-2023 ERROR"


@pytest.mark.asyncio
async def test_processor_lock_lifecycle():
    """start -> is_processing -> end -> not processing"""
    proc = _make_processor()

    assert await proc.is_processing("fetch", "12345", "TP", 2023) is False
    await proc.start_processing("fetch", "12345", "TP", 2023)
    assert await proc.is_processing("fetch", "12345", "TP", 2023) is True
    await proc.end_processing("fetch", "12345", "TP", 2023)
    assert await proc.is_processing("fetch", "12345", "TP", 2023) is False


@pytest.mark.asyncio
async def test_processor_refresh_checks_both_queues():
    """refresh is_processing returns True if app is in either fetch or refresh queue"""
    proc = _make_processor()

    await proc.start_processing("fetch", "12345", "TP", 2023)
    assert await proc.is_processing("refresh", "12345", "TP", 2023) is True

    await proc.end_processing("fetch", "12345", "TP", 2023)
    await proc.start_processing("refresh", "12345", "TP", 2023)
    assert await proc.is_processing("refresh", "12345", "TP", 2023) is True


# ---------------------------------------------------------------------------
# ZOV-specific tests (Stage 1.3 changes)
# ---------------------------------------------------------------------------

_ZOV_BASE_MSG = {
    "chat_id": 200,
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


def _make_zov_db_row(**overrides):
    """Build a fake DB row for a ZOV application"""
    from datetime import datetime
    row = {
        "chat_id": 200,
        "application_number": "ISTA202504220001",
        "application_suffix": "0",
        "application_type": "ZOV",
        "application_year": 0,
        "last_updated": datetime(2025, 4, 22),
        "application_state": "IN_PROGRESS",
        "application_source": "zov",
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_monitor_check_for_updates_zov_source():
    """check_for_updates includes source='zov' in message for ZOV apps"""
    db = AsyncMock()
    rabbit = AsyncMock()
    db.fetch_applications_needing_update = AsyncMock(return_value=[_make_zov_db_row()])

    monitor = ApplicationMonitor(db, rabbit)
    await monitor.check_for_updates()

    published_msg = rabbit.publish_message.call_args[0][0]
    assert published_msg["source"] == "zov"
    assert published_msg["number"] == "ISTA202504220001"


@pytest.mark.asyncio
async def test_monitor_expire_stale_zov_source():
    """expire_stale_not_found_applications includes source='zov' for ZOV apps"""
    from datetime import datetime
    db = AsyncMock()
    rabbit = AsyncMock()
    row = _make_zov_db_row(
        application_id=99,
        created_at=datetime(2025, 1, 1),
        application_state="NOT_FOUND",
    )
    db.fetch_applications_to_expire = AsyncMock(return_value=[row])

    monitor = ApplicationMonitor(db, rabbit)
    await monitor.expire_stale_not_found_applications()

    published_msg = rabbit.publish_message.call_args[0][0]
    assert published_msg["source"] == "zov"


@pytest.mark.asyncio
async def test_reminder_trigger_reminders_zov_source():
    """trigger_reminders includes source='zov' for ZOV apps"""
    from datetime import datetime
    db = AsyncMock()
    rabbit = AsyncMock()
    db.fetch_due_reminders = AsyncMock(return_value=[_make_zov_db_row()])

    reminder_mon = ReminderMonitor(db, rabbit)
    await reminder_mon.trigger_reminders()

    published_msg = rabbit.publish_message.call_args[0][0]
    assert published_msg["source"] == "zov"


@pytest.mark.asyncio
async def test_monitor_check_for_updates_oam_source():
    """check_for_updates includes source='oam' for OAM apps"""
    db = AsyncMock()
    rabbit = AsyncMock()
    db.fetch_applications_needing_update = AsyncMock(return_value=[_make_oam_db_row()])

    monitor = ApplicationMonitor(db, rabbit)
    await monitor.check_for_updates()

    published_msg = rabbit.publish_message.call_args[0][0]
    assert published_msg["source"] == "oam"


def test_processor_generate_error_message_zov():
    proc = _make_processor()
    app_details = {
        "number": "ISTA202504220001", "suffix": "0",
        "type": "ZOV", "year": 0, "source": "zov",
    }
    result = proc._generate_error_message(app_details)
    assert result == "ISTA202504220001 ERROR"


def test_processor_generate_error_message_oam_no_source():
    """Without source key, defaults to OAM format"""
    proc = _make_processor()
    app_details = {"number": "12345", "suffix": "0", "type": "TP", "year": "2023"}
    result = proc._generate_error_message(app_details)
    assert result == "OAM-12345-0/TP-2023 ERROR"


@pytest.mark.asyncio
async def test_rabbit_on_update_zov_status_changed():
    """ZOV status change flows through on_update_message correctly"""
    rabbit = _make_rabbit()
    old_status = "Visa application number ISTA202504220001 not found"
    new_status = "Visa application number ISTA202504220001 is still being processed"
    rabbit.db.fetch_application_status = AsyncMock(return_value=old_status)
    rabbit.db.update_application_status = AsyncMock(return_value=True)
    rabbit.db.fetch_user_language = AsyncMock(return_value="EN")

    msg_dict = {**_ZOV_BASE_MSG, "status": new_status}
    msg = _make_incoming_message(msg_dict)

    with patch("bot.rabbitmq.notify_user", new_callable=AsyncMock) as mock_notify:
        await rabbit.on_update_message(msg)
        rabbit.db.update_application_status.assert_called_once()
        call_args = rabbit.db.update_application_status.call_args[0]
        assert call_args[5] is False  # in_progress is NOT resolved
        mock_notify.assert_called_once()


@pytest.mark.asyncio
async def test_rabbit_on_expiration_zov():
    """ZOV expiration message uses correct identifier in notification"""
    rabbit = _make_rabbit()
    rabbit.db.resolve_application = AsyncMock(return_value=True)
    rabbit.db.fetch_user_language = AsyncMock(return_value="EN")

    msg_dict = {**_ZOV_BASE_MSG, "application_id": 99, "request_type": "expire"}
    msg = _make_incoming_message(msg_dict)

    with patch("bot.rabbitmq.notify_user", new_callable=AsyncMock) as mock_notify:
        await rabbit.on_expiration_message(msg)
        rabbit.db.resolve_application.assert_called_once_with(99)
        notification_text = mock_notify.call_args[0][2]
        assert "ISTA202504220001" in notification_text


def test_rabbit_generate_unique_id_zov():
    """ZOV messages produce valid unique IDs"""
    rabbit = _make_rabbit()
    uid = rabbit.generate_unique_id(_ZOV_BASE_MSG)
    assert isinstance(uid, str) and len(uid) == 32


@pytest.mark.asyncio
async def test_rabbit_on_update_pre_approved_notifies_user():
    """pre_approved status change should update DB and notify the user"""
    rabbit = _make_rabbit()
    old_status = "Visa application number ISTA202504220001 not found"
    new_status = "Visa application number ISTA202504220001 has been preliminarily assessed positively"
    rabbit.db.fetch_application_status = AsyncMock(return_value=old_status)
    rabbit.db.update_application_status = AsyncMock(return_value=True)
    rabbit.db.fetch_user_language = AsyncMock(return_value="EN")

    msg_dict = {**_ZOV_BASE_MSG, "status": new_status}
    msg = _make_incoming_message(msg_dict)

    with patch("bot.rabbitmq.notify_user", new_callable=AsyncMock) as mock_notify:
        await rabbit.on_update_message(msg)
        rabbit.db.update_application_status.assert_called_once()
        call_args = rabbit.db.update_application_status.call_args[0]
        assert call_args[5] is False  # pre_approved is NOT resolved
        mock_notify.assert_called_once()
