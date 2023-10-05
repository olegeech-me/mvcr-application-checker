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


@patch("bot.handlers.ALLOWED_YEARS", [2020, 2021, 2022, 2023, 2042])
@patch("bot.handlers.ALLOWED_TYPES", ["MK", "DO", "TP"])
@pytest.mark.parametrize(
    "num_str, app_num, app_suffix, app_type, app_year",
    [
        ("OAM-4242/TP-2042", "4242", "0", "TP", "2042"),
        ("4242-5/DO-2020", "4242", "5", "DO", "2020"),
        ("oAM-12345-9/MK-2023", "12345", "9", "MK", "2023"),
        ("BAD-NUMBER/MK-2023", None, None, None, None),
        ("oam-4242-6/MK-1999", None, None, None, None),
        ("oam-4242-6/NT-2021", None, None, None, None),
    ],
)
def test__parse_application_number_full(num_str, app_num, app_suffix, app_type, app_year):
    res = _parse_application_number_full(num_str)
    if res:
        assert res == (app_num, app_suffix, app_type, app_year)
    else:
        assert res is None


@pytest.mark.parametrize(
    "num_str, app_num, app_suffix",
    [
        ("OAM-4242/TP-2042", "4242", "0"),
        ("4242-5/DO-2020", "4242", "5"),
        ("oAM-12345-9/MK-2023", "12345", "9"),
        ("BAD-NUMBER/MK-2023", None, None),
    ],
)
def test__parse_application_number(num_str, app_num, app_suffix):
    res = _parse_application_number(num_str)
    if res:
        assert res == (app_num, app_suffix, app_type, app_year)
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
    db_mock.get_user_language = AsyncMock(return_value=user_lang_db)

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
    result = check_and_update_limit(user_data, "test_command")
    assert result is True
    # Assume 2 as the limit. The 3rd time it should return False.
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

    # Testing rate limit for the first time, should return True
    result = asyncio.run(enforce_rate_limit(update, context, "test_command"))
    assert result

    # Testing rate limit for the second time, should still return True
    result = asyncio.run(enforce_rate_limit(update, context, "test_command"))
    assert result

    # Testing rate limit for the third time, should return False
    result = asyncio.run(enforce_rate_limit(update, context, "test_command"))
    assert not result


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
