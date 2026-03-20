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


# @pytest.fixture
# def mock_rabbit(mocker):
#    bot = AsyncMock()
#    db = AsyncMock()
#    loop = asyncio.get_event_loop()
#    mocker.patch("aiormq.Connection", AsyncMock())
#    rabbit = RabbitMQ("host", "user", "password", bot, db, loop)
#
#    # Mocking RabbitMQ connections and channels
#    rabbit.connection = AsyncMock()
#    rabbit.channel = AsyncMock()
#    rabbit.queue = AsyncMock()
#    rabbit.default_exchange = AsyncMock()
#
#    return rabbit
#
#
# @pytest.mark.asyncio
# async def test_connect_success(mock_rabbit):
#    await mock_rabbit.connect()
#    mock_rabbit.channel.declare_queue.assert_called_once_with("StatusUpdateQueue", durable=True)
#
#
# @pytest.mark.asyncio
# async def test_on_message_no_change(mock_rabbit):
#    mock_msg = AsyncMock()
#    mock_msg.body = json.dumps(
#        {
#            "chat_id": "123",
#            "status": "test_status",
#            "number": "12345",
#            "last_updated": "now",
#            "force_refresh": False,
#        }
#    ).encode("utf-8")
#
#    mock_rabbit.db.get_application_status = AsyncMock(return_value="test_status")
#    await mock_rabbit.on_message(mock_msg)
#    mock_rabbit.bot.updater.bot.send_message.assert_not_called()
