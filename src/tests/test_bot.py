import pytest
import os
from unittest.mock import Mock, AsyncMock, patch
import asyncio

os.environ["RUN_MODE"] = "TEST"
os.environ["ADMIN_CHAT_ID"] = "1234567"

from bot.handlers import (
    _parse_application_number_full,
    _parse_application_number,
    _get_user_language,
    _is_admin,
    user_info,
    _show_app_number_final_confirmation,
    check_and_update_limit,
    create_request,
)


@pytest.mark.parametrize(
    "num_str, app_num, app_suffix, app_type, app_year",
    [
        ("OAM-4242/TP-2042", "4242", "0", "TP", "2042"),
        ("4242-5/DO-2020", "4242", "5", "DO", "2020"),
        ("oAM-12345-9/MK-2023", "12345", "9", "MK", "2023"),
        ("BAD-NUMBER/MK-2023", None, None, None, None),
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


def test_is_admin():
    assert _is_admin("1234567") is True
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
