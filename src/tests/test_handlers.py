import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest
from conftest import make_zov_subscription

from bot.handlers import (
    BROADCAST_CONFIRM,
    BROADCAST_TEXT,
    BUTTON_WAIT_SECONDS,
    NUMBER,
    SOURCE,
    TYPE,
    VALIDATE,
    _broadcast_to_users,
    _generate_buttons_from_subscriptions,
    _get_user_language,
    _is_admin,
    _is_button_click_abused,
    _parse_application_buttons_callback_data,
    _parse_application_number,
    _parse_application_number_full,
    _parse_zov_number,
    _show_app_number_final_confirmation,
    _sync_user_profile,
    admin_broadcast_command,
    admin_broadcast_confirm,
    admin_broadcast_text,
    application_dialog_number,
    application_dialog_source,
    check_and_update_limit,
    clean_sub_context,
    create_request,
    create_subscription,
    enforce_rate_limit,
    fetcher_stats_command,
    force_refresh_button,
    force_refresh_command,
    subscribe_command,
    unsubscribe_button,
    unsubscribe_command,
    user_info,
)

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


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
    "input_str, expected",
    [
        ("ISTA202504220001", "ISTA202504220001"),
        ("ISTA202504220", "ISTA202504220"),
        ("ista202504220001", "ISTA202504220001"),
        ("MOSK202503030001", "MOSK202503030001"),
        ("KYJV202601150", "KYJV202601150"),
        ("ISTA2025", None),
        ("12345/TP-2023", None),
        ("ISTAX2025042200", None),
        ("IST2025042200010", None),
        ("", None),
    ],
)
def test_parse_zov_number(input_str, expected):
    result = _parse_zov_number(input_str)
    assert result == expected


# ---------------------------------------------------------------------------
# User helpers
# ---------------------------------------------------------------------------


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
    db_mock.reactivate_user_if_needed = AsyncMock(return_value=False)

    with patch("bot.handlers.db", db_mock), \
         patch("bot.handlers._sync_user_profile", new_callable=AsyncMock):
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
    assert _is_admin("56745679") is True
    assert _is_admin("123456789") is False


def test_user_info():
    update = Mock()
    update.effective_chat.id = 12345
    update.effective_chat.username = "testuser"
    update.effective_chat.first_name = "Vasya"
    update.effective_chat.last_name = "Pupkin"

    result = user_info(update)
    assert result == "first_name: Vasya, last_name: Pupkin, username: testuser, chat_id: 12345"


def test_user_info_partial():
    """user_info with missing optional fields omits them"""
    update = Mock()
    update.effective_chat.id = 12345
    update.effective_chat.username = None
    update.effective_chat.first_name = "Vasya"
    update.effective_chat.last_name = None

    result = user_info(update)
    assert result == "first_name: Vasya, chat_id: 12345"


# ---------------------------------------------------------------------------
# Profile sync
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_user_profile_new_user():
    """New user (not in DB) triggers insert"""
    mock_db = AsyncMock()
    mock_db.fetch_user_profile = AsyncMock(return_value=None)
    mock_db.insert_user = AsyncMock(return_value=True)

    update = Mock()
    update.effective_chat.id = 100
    update.effective_chat.username = "newuser"
    update.effective_chat.first_name = "New"
    update.effective_chat.last_name = "User"

    with patch("bot.handlers.db", mock_db):
        await _sync_user_profile(update)

    mock_db.insert_user.assert_called_once_with(100, "New", "newuser", "User", lang="EN")
    mock_db.update_user_profile = AsyncMock()
    mock_db.update_user_profile.assert_not_called()


@pytest.mark.asyncio
async def test_sync_user_profile_no_change():
    """Existing user with unchanged profile does not trigger update"""
    mock_db = AsyncMock()
    mock_db.fetch_user_profile = AsyncMock(return_value={
        "username": "vasya123", "first_name": "Vasya", "last_name": "Pupkin",
    })
    mock_db.update_user_profile = AsyncMock()

    update = Mock()
    update.effective_chat.id = 100
    update.effective_chat.username = "vasya123"
    update.effective_chat.first_name = "Vasya"
    update.effective_chat.last_name = "Pupkin"

    with patch("bot.handlers.db", mock_db):
        await _sync_user_profile(update)

    mock_db.insert_user.assert_not_called()
    mock_db.update_user_profile.assert_not_called()


@pytest.mark.asyncio
async def test_sync_user_profile_username_changed():
    """Username change triggers profile update"""
    mock_db = AsyncMock()
    mock_db.fetch_user_profile = AsyncMock(return_value={
        "username": "old_name", "first_name": "Vasya", "last_name": "Pupkin",
    })
    mock_db.update_user_profile = AsyncMock()

    update = Mock()
    update.effective_chat.id = 100
    update.effective_chat.username = "new_name"
    update.effective_chat.first_name = "Vasya"
    update.effective_chat.last_name = "Pupkin"

    with patch("bot.handlers.db", mock_db):
        await _sync_user_profile(update)

    mock_db.update_user_profile.assert_called_once_with(100, "new_name", "Vasya", "Pupkin")


@pytest.mark.asyncio
async def test_sync_user_profile_first_name_changed():
    """First name change triggers profile update"""
    mock_db = AsyncMock()
    mock_db.fetch_user_profile = AsyncMock(return_value={
        "username": "vasya123", "first_name": "Vasya", "last_name": "Pupkin",
    })
    mock_db.update_user_profile = AsyncMock()

    update = Mock()
    update.effective_chat.id = 100
    update.effective_chat.username = "vasya123"
    update.effective_chat.first_name = "Vasiliy"
    update.effective_chat.last_name = "Pupkin"

    with patch("bot.handlers.db", mock_db):
        await _sync_user_profile(update)

    mock_db.update_user_profile.assert_called_once_with(100, "vasya123", "Vasiliy", "Pupkin")


@pytest.mark.asyncio
async def test_sync_user_profile_field_removed():
    """Field going from a value to None triggers update"""
    mock_db = AsyncMock()
    mock_db.fetch_user_profile = AsyncMock(return_value={
        "username": "vasya123", "first_name": "Vasya", "last_name": "Pupkin",
    })
    mock_db.update_user_profile = AsyncMock()

    update = Mock()
    update.effective_chat.id = 100
    update.effective_chat.username = None
    update.effective_chat.first_name = "Vasya"
    update.effective_chat.last_name = "Pupkin"

    with patch("bot.handlers.db", mock_db):
        await _sync_user_profile(update)

    mock_db.update_user_profile.assert_called_once_with(100, None, "Vasya", "Pupkin")


# ---------------------------------------------------------------------------
# _get_user_language triggers profile sync
# ---------------------------------------------------------------------------


def test_get_user_language_triggers_sync_on_first_call():
    """First call (no cached lang) triggers _sync_user_profile"""
    mock_db = Mock()
    mock_db.fetch_user_language = AsyncMock(return_value="RU")
    mock_db.reactivate_user_if_needed = AsyncMock(return_value=False)

    with patch("bot.handlers.db", mock_db), \
         patch("bot.handlers._sync_user_profile", new_callable=AsyncMock) as mock_sync:
        update = Mock()
        update.effective_chat.id = 100
        context = Mock()
        context.user_data = {}

        asyncio.run(_get_user_language(update, context))
        mock_sync.assert_called_once_with(update)


def test_get_user_language_skips_sync_on_cached():
    """Second call (cached lang) does NOT trigger _sync_user_profile"""
    mock_db = Mock()
    mock_db.reactivate_user_if_needed = AsyncMock(return_value=False)

    with patch("bot.handlers.db", mock_db), \
         patch("bot.handlers._sync_user_profile", new_callable=AsyncMock) as mock_sync:
        update = Mock()
        update.effective_chat.id = 100
        context = Mock()
        context.user_data = {"lang": "EN"}

        asyncio.run(_get_user_language(update, context))
        mock_sync.assert_not_called()


def test_get_user_language_reactivates_on_every_call():
    """reactivate_user_if_needed must run on every call, including the cached path,
    so a user who blocked then unblocked the bot starts receiving notifications again
    """
    mock_db = Mock()
    mock_db.reactivate_user_if_needed = AsyncMock(return_value=False)

    with patch("bot.handlers.db", mock_db), \
         patch("bot.handlers._sync_user_profile", new_callable=AsyncMock):
        update = Mock()
        update.effective_chat.id = 42
        context = Mock()
        context.user_data = {"lang": "EN"}

        asyncio.run(_get_user_language(update, context))

    mock_db.reactivate_user_if_needed.assert_awaited_once_with(42)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_check_and_update_limit():
    user_data = {}
    for _attempt in range(5):
        result = check_and_update_limit(user_data, "test_command")
        assert result is True

    result = check_and_update_limit(user_data, "test_command")
    assert result is False


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

    result = asyncio.run(enforce_rate_limit(update, context, "test_command"))
    assert not result


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------


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
    update = Mock()
    update.callback_query = AsyncMock()
    context = Mock()
    context.user_data = {}

    is_abuse = await _is_button_click_abused(update, context)
    assert not is_abuse

    is_abuse = await _is_button_click_abused(update, context)
    assert is_abuse

    await asyncio.sleep(BUTTON_WAIT_SECONDS + 0.1)
    is_abuse = await _is_button_click_abused(update, context)
    assert not is_abuse


@pytest.mark.asyncio
async def test_show_confirmation_zov():
    """ZOV confirmation uses ZOV-specific message without OAM prefix"""
    update = Mock()
    update.callback_query = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    context = Mock()
    context.user_data = {
        "application_number": "ISTA202504220001",
        "application_suffix": "0",
        "application_type": "ZOV",
        "application_year": 0,
    }

    with patch("bot.handlers._get_user_language", new_callable=AsyncMock, return_value="EN"):
        await _show_app_number_final_confirmation(update, context)
        call_args = update.callback_query.edit_message_text.call_args
        msg_text = call_args[0][0]
        assert "ISTA202504220001" in msg_text
        assert "OAM" not in msg_text


# ---------------------------------------------------------------------------
# Context cleanup, buttons, callback parsing
# ---------------------------------------------------------------------------


def test_clean_sub_context_removes_oam_keys():
    """clean_sub_context removes OAM subscription keys, preserves others"""
    context = Mock()
    context.user_data = {
        "application_number": "4242",
        "application_suffix": "0",
        "application_type": "TP",
        "application_year": "2023",
        "application_source": "oam",
        "lang": "EN",
        "last_button_press": 123456,
    }
    clean_sub_context(context)
    assert "application_number" not in context.user_data
    assert "application_suffix" not in context.user_data
    assert "application_type" not in context.user_data
    assert "application_year" not in context.user_data
    assert "application_source" not in context.user_data
    assert context.user_data["lang"] == "EN"
    assert context.user_data["last_button_press"] == 123456


def test_generate_buttons_oam_no_suffix():
    """OAM subscription without suffix produces correct button label and callback"""
    subs = [{"application_number": "12345", "application_suffix": "0", "application_type": "TP", "application_year": 2023}]
    result = _generate_buttons_from_subscriptions("status", subs)
    buttons = result.inline_keyboard
    assert len(buttons) == 1
    assert buttons[0][0].text == "OAM-12345/TP-2023"
    assert buttons[0][0].callback_data == "status_12345-TP-2023"


def test_generate_buttons_oam_with_suffix():
    """OAM subscription with suffix includes it in the label"""
    subs = [{"application_number": "4242", "application_suffix": "5", "application_type": "DO", "application_year": 2020}]
    result = _generate_buttons_from_subscriptions("unsubscribe", subs)
    buttons = result.inline_keyboard
    assert buttons[0][0].text == "OAM-4242-5/DO-2020"
    assert buttons[0][0].callback_data == "unsubscribe_4242-DO-2020"


def test_generate_buttons_multiple_subscriptions():
    """Multiple subscriptions produce one button row each"""
    subs = [
        {"application_number": "100", "application_suffix": "0", "application_type": "TP", "application_year": 2023},
        {"application_number": "200", "application_suffix": "3", "application_type": "MK", "application_year": 2022},
    ]
    result = _generate_buttons_from_subscriptions("force_refresh", subs)
    buttons = result.inline_keyboard
    assert len(buttons) == 2
    assert buttons[0][0].text == "OAM-100/TP-2023"
    assert buttons[1][0].text == "OAM-200-3/MK-2022"


def test_generate_buttons_zov_subscription():
    """ZOV subscription uses ZOV number as button label"""
    subs = [
        {
            "application_number": "ISTA202504220001",
            "application_suffix": "0",
            "application_type": "ZOV",
            "application_year": 0,
        }
    ]
    result = _generate_buttons_from_subscriptions("status", subs)
    buttons = result.inline_keyboard
    assert buttons[0][0].text == "ISTA202504220001"
    assert buttons[0][0].callback_data == "status_ISTA202504220001-ZOV-0"


def test_generate_buttons_mixed_oam_zov():
    """Mixed OAM+ZOV subscriptions produce correct labels for both"""
    subs = [
        {
            "application_number": "12345",
            "application_suffix": "0",
            "application_type": "TP",
            "application_year": 2023,
        },
        {
            "application_number": "ISTA202504220001",
            "application_suffix": "0",
            "application_type": "ZOV",
            "application_year": 0,
        },
    ]
    result = _generate_buttons_from_subscriptions("status", subs)
    buttons = result.inline_keyboard
    assert len(buttons) == 2
    assert buttons[0][0].text == "OAM-12345/TP-2023"
    assert buttons[1][0].text == "ISTA202504220001"


def test_parse_buttons_callback_data_oam():
    """Parses OAM button callback data into correct dict"""
    result = _parse_application_buttons_callback_data("status_12345-TP-2023")
    assert result == {"number": "12345", "type": "TP", "year": 2023}


def test_parse_buttons_callback_data_force_refresh_prefix():
    """Parses multi-underscore prefix correctly (takes last segment)"""
    result = _parse_application_buttons_callback_data("force_refresh_4242-DO-2020")
    assert result == {"number": "4242", "type": "DO", "year": 2020}


# ---------------------------------------------------------------------------
# Request building
# ---------------------------------------------------------------------------


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


def test_create_request_includes_user_fields():
    """create_request includes username/first_name/last_name when provided"""
    app_data = {"number": "4242", "suffix": "0", "type": "TP", "year": "2042"}
    result = create_request(100, app_data, username="vasya", first_name="Vasya", last_name="Pupkin")
    assert result["username"] == "vasya"
    assert result["first_name"] == "Vasya"
    assert result["last_name"] == "Pupkin"


def test_create_request_user_fields_default_none():
    """create_request without user fields defaults them to None"""
    app_data = {"number": "4242", "suffix": "0", "type": "TP", "year": "2042"}
    result = create_request(100, app_data)
    assert result["username"] is None
    assert result["first_name"] is None
    assert result["last_name"] is None


def test_create_request_zov_derives_source():
    """ZOV create_request derives source='zov' from type, no explicit source needed"""
    app_data = {"number": "ISTA202504220001", "suffix": "0", "type": "ZOV", "year": 0}
    result = create_request(100, app_data)
    assert result["source"] == "zov"
    assert result["number"] == "ISTA202504220001"
    assert result["type"] == "ZOV"
    assert result["year"] == 0


def test_create_request_from_parsed_zov_callback():
    """create_request derives source='zov' from parsed ZOV callback type"""
    subs = [make_zov_subscription()]
    markup = _generate_buttons_from_subscriptions("force_refresh", subs)
    callback_data = markup.inline_keyboard[0][0].callback_data

    parsed = _parse_application_buttons_callback_data(callback_data)
    request = create_request(200, parsed, force_refresh=True)
    assert request["number"] == "ISTA202504220001"
    assert request["force_refresh"] is True
    assert request["source"] == "zov"


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_subscription_oam_happy_path():
    """OAM subscription: inserts into DB, publishes to queue, sends completion"""
    mock_db = AsyncMock()
    mock_db.insert_application = AsyncMock(return_value=True)
    mock_rabbit = AsyncMock()

    update = Mock()
    update.effective_chat.id = 100
    update.callback_query.message.reply_text = AsyncMock()

    app_data = {"number": "4242", "suffix": "0", "type": "TP", "year": "2023"}

    with patch("bot.handlers.db", mock_db), patch("bot.handlers.rabbit", mock_rabbit):
        await create_subscription(update, app_data, lang="EN")
        mock_db.insert_application.assert_called_once_with(100, "4242", "0", "TP", 2023)
        mock_rabbit.publish_message.assert_called_once()
        update.callback_query.message.reply_text.assert_called_once()


@pytest.mark.asyncio
async def test_create_subscription_oam_db_failure():
    """DB insert failure skips queue publish"""
    mock_db = AsyncMock()
    mock_db.insert_application = AsyncMock(return_value=False)
    mock_rabbit = AsyncMock()

    update = Mock()
    update.effective_chat.id = 100
    update.callback_query.message.reply_text = AsyncMock()

    app_data = {"number": "4242", "suffix": "0", "type": "TP", "year": "2023"}

    with patch("bot.handlers.db", mock_db), patch("bot.handlers.rabbit", mock_rabbit):
        await create_subscription(update, app_data, lang="EN")
        mock_rabbit.publish_message.assert_not_called()
        update.callback_query.message.reply_text.assert_called_once()


@pytest.mark.asyncio
async def test_create_subscription_zov_publishes_source():
    """ZOV subscription publishes RabbitMQ message with source='zov'"""
    mock_db = AsyncMock()
    mock_db.insert_application = AsyncMock(return_value=True)
    mock_rabbit = AsyncMock()

    update = Mock()
    update.effective_chat.id = 200
    update.callback_query.message.reply_text = AsyncMock()

    app_data = {"number": "ISTA202504220001", "suffix": "0", "type": "ZOV", "year": 0}

    with patch("bot.handlers.db", mock_db), patch("bot.handlers.rabbit", mock_rabbit):
        await create_subscription(update, app_data, lang="EN")

    mock_rabbit.publish_message.assert_called_once()
    published_msg = mock_rabbit.publish_message.call_args[0][0]
    assert published_msg["source"] == "zov"
    assert published_msg["number"] == "ISTA202504220001"
    assert published_msg["type"] == "ZOV"


# ---------------------------------------------------------------------------
# Dialog flows
# ---------------------------------------------------------------------------


@patch("bot.handlers.get_allowed_years", return_value=[2020, 2021, 2022, 2023])
@pytest.mark.asyncio
async def test_application_dialog_number_full_oam(mock_years):
    """Full OAM input sets context and jumps to VALIDATE"""
    update = Mock()
    update.edited_message = None
    update.message.text = "12345/TP-2023"
    context = Mock()
    context.user_data = {"application_source": "oam"}

    with patch("bot.handlers._get_user_language", new_callable=AsyncMock, return_value="EN"), patch(
        "bot.handlers._show_app_number_final_confirmation", new_callable=AsyncMock
    ):
        result = await application_dialog_number(update, context)
        assert result == VALIDATE
        assert context.user_data["application_number"] == "12345"
        assert context.user_data["application_suffix"] == "0"
        assert context.user_data["application_type"] == "TP"
        assert context.user_data["application_year"] == "2023"


@pytest.mark.asyncio
async def test_application_dialog_number_partial_oam():
    """Partial OAM input (number only) sets number/suffix and returns TYPE"""
    update = Mock()
    update.edited_message = None
    update.message.text = "12345"
    update.message.reply_text = AsyncMock()
    context = Mock()
    context.user_data = {"application_source": "oam"}

    with patch("bot.handlers._get_user_language", new_callable=AsyncMock, return_value="EN"):
        result = await application_dialog_number(update, context)
        assert result == TYPE
        assert context.user_data["application_number"] == "12345"
        assert context.user_data["application_suffix"] == "0"


@pytest.mark.asyncio
async def test_application_dialog_number_invalid_input():
    """Invalid input sends error message and returns None"""
    update = Mock()
    update.edited_message = None
    update.message.text = "BADINPUT!!!"
    update.message.reply_text = AsyncMock()
    context = Mock()
    context.user_data = {"application_source": "oam"}

    with patch("bot.handlers._get_user_language", new_callable=AsyncMock, return_value="EN"):
        result = await application_dialog_number(update, context)
        assert result is None
        update.message.reply_text.assert_called_once()


@pytest.mark.asyncio
async def test_application_dialog_number_zov():
    """ZOV input sets source context and jumps to VALIDATE"""
    update = Mock()
    update.edited_message = None
    update.message.text = "ISTA202504220001"
    update.message.reply_text = AsyncMock()
    context = Mock()
    context.user_data = {"application_source": "zov"}

    with patch("bot.handlers._get_user_language", new_callable=AsyncMock, return_value="EN"), patch(
        "bot.handlers._show_app_number_final_confirmation", new_callable=AsyncMock
    ):
        result = await application_dialog_number(update, context)
        assert result == VALIDATE
        assert context.user_data["application_number"] == "ISTA202504220001"
        assert context.user_data["application_suffix"] == "0"
        assert context.user_data["application_type"] == "ZOV"
        assert context.user_data["application_year"] == 0


@pytest.mark.asyncio
async def test_application_dialog_source_oam():
    """Selecting OAM source stores it and returns NUMBER"""
    update = Mock()
    update.callback_query = AsyncMock()
    update.callback_query.data = "application_source_oam"
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    context = Mock()
    context.user_data = {}

    with patch("bot.handlers._get_user_language", new_callable=AsyncMock, return_value="EN"), \
         patch("bot.handlers._is_button_click_abused", new_callable=AsyncMock, return_value=False):
        result = await application_dialog_source(update, context)
        assert result == NUMBER
        assert context.user_data["application_source"] == "oam"
        update.callback_query.edit_message_text.assert_called_once()
        call_args = update.callback_query.edit_message_text.call_args
        assert "OAM-" in call_args[0][0]


@pytest.mark.asyncio
async def test_application_dialog_source_zov():
    """Selecting ZOV source stores it and returns NUMBER"""
    update = Mock()
    update.callback_query = AsyncMock()
    update.callback_query.data = "application_source_zov"
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    context = Mock()
    context.user_data = {}

    with patch("bot.handlers._get_user_language", new_callable=AsyncMock, return_value="EN"), \
         patch("bot.handlers._is_button_click_abused", new_callable=AsyncMock, return_value=False):
        result = await application_dialog_source(update, context)
        assert result == NUMBER
        assert context.user_data["application_source"] == "zov"
        update.callback_query.edit_message_text.assert_called_once()
        call_args = update.callback_query.edit_message_text.call_args
        assert "visa" in call_args[0][0].lower()


@pytest.mark.asyncio
async def test_application_dialog_number_zov_rejects_oam_input():
    """ZOV source rejects OAM-format input with ZOV-specific error"""
    update = Mock()
    update.edited_message = None
    update.message.text = "12345/TP-2023"
    update.message.reply_text = AsyncMock()
    context = Mock()
    context.user_data = {"application_source": "zov"}

    with patch("bot.handlers._get_user_language", new_callable=AsyncMock, return_value="EN"):
        result = await application_dialog_number(update, context)
        assert result is None
        update.message.reply_text.assert_called_once()
        call_args = update.message.reply_text.call_args
        assert "visa" in call_args[0][0].lower()


@pytest.mark.asyncio
async def test_application_dialog_number_oam_rejects_zov_input():
    """OAM source rejects ZOV-format input with OAM error"""
    update = Mock()
    update.edited_message = None
    update.message.text = "ISTA202504220001"
    update.message.reply_text = AsyncMock()
    context = Mock()
    context.user_data = {"application_source": "oam"}

    with patch("bot.handlers._get_user_language", new_callable=AsyncMock, return_value="EN"):
        result = await application_dialog_number(update, context)
        assert result is None
        update.message.reply_text.assert_called_once()


# ---------------------------------------------------------------------------
# Commands: subscribe
# ---------------------------------------------------------------------------


@patch("bot.handlers.get_allowed_years", return_value=[2020, 2021, 2022, 2023])
@pytest.mark.asyncio
async def test_subscribe_command_with_oam_args(mock_years):
    """subscribe with full OAM args sets context and returns VALIDATE"""
    update = Mock()
    update.edited_message = None
    update.message.text = "/subscribe 12345/TP-2023"
    update.message.chat_id = 100
    update.effective_chat.id = 100
    update.effective_chat.first_name = "Test"
    update.effective_chat.username = "testuser"
    update.effective_chat.last_name = "User"
    context = Mock()
    context.args = ["12345/TP-2023"]
    context.user_data = {}

    mock_db = AsyncMock()
    mock_db.count_user_subscriptions = AsyncMock(return_value=0)
    mock_db.user_exists = AsyncMock(return_value=True)

    with patch("bot.handlers._get_user_language", new_callable=AsyncMock, return_value="EN"), patch(
        "bot.handlers.db", mock_db
    ), patch("bot.handlers._show_app_number_final_confirmation", new_callable=AsyncMock):
        result = await subscribe_command(update, context)
        assert result == VALIDATE
        assert context.user_data["application_number"] == "12345"
        assert context.user_data["application_type"] == "TP"
        assert context.user_data["application_year"] == "2023"


@pytest.mark.asyncio
async def test_subscribe_command_no_args_returns_source():
    """subscribe with no args shows source selection and returns SOURCE"""
    update = Mock()
    update.edited_message = None
    update.message = AsyncMock()
    update.message.chat_id = 100
    update.message.reply_text = AsyncMock()
    update.effective_chat.id = 100
    update.effective_chat.first_name = "Test"
    update.effective_chat.username = "testuser"
    update.effective_chat.last_name = "User"
    context = Mock()
    context.args = []
    context.user_data = {}

    mock_db = AsyncMock()
    mock_db.count_user_subscriptions = AsyncMock(return_value=0)
    mock_db.user_exists = AsyncMock(return_value=True)

    with patch("bot.handlers._get_user_language", new_callable=AsyncMock, return_value="EN"), patch("bot.handlers.db", mock_db):
        result = await subscribe_command(update, context)
        assert result == SOURCE
        update.message.reply_text.assert_called_once()


@pytest.mark.asyncio
async def test_subscribe_command_with_zov_args():
    """subscribe with ZOV args sets ZOV context and returns VALIDATE"""
    update = Mock()
    update.edited_message = None
    update.message.text = "/subscribe ISTA202504220001"
    update.message.chat_id = 100
    update.message.reply_text = AsyncMock()
    update.effective_chat.id = 100
    update.effective_chat.first_name = "Test"
    update.effective_chat.username = "testuser"
    update.effective_chat.last_name = "User"
    context = Mock()
    context.args = ["ISTA202504220001"]
    context.user_data = {}

    mock_db = AsyncMock()
    mock_db.count_user_subscriptions = AsyncMock(return_value=0)
    mock_db.user_exists = AsyncMock(return_value=True)

    with patch("bot.handlers._get_user_language", new_callable=AsyncMock, return_value="EN"), patch(
        "bot.handlers.db", mock_db
    ), patch("bot.handlers._show_app_number_final_confirmation", new_callable=AsyncMock):
        result = await subscribe_command(update, context)
        assert result == VALIDATE
        assert context.user_data["application_number"] == "ISTA202504220001"
        assert context.user_data["application_type"] == "ZOV"
        assert "application_source" not in context.user_data


# ---------------------------------------------------------------------------
# Commands: force_refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_force_refresh_command_single_oam():
    """force_refresh with one OAM subscription publishes correct message"""
    oam_sub = {
        "application_number": "12345",
        "application_suffix": "0",
        "application_type": "TP",
        "application_year": 2023,
    }
    mock_db = AsyncMock()
    mock_db.fetch_user_subscriptions = AsyncMock(return_value=[oam_sub])
    mock_rabbit = AsyncMock()

    update = Mock()
    update.edited_message = None
    update.message = AsyncMock()
    update.message.text = "/force_refresh"
    update.message.reply_text = AsyncMock()
    update.effective_chat.id = 100
    context = Mock()
    context.user_data = {}

    with patch("bot.handlers.db", mock_db), \
         patch("bot.handlers.rabbit", mock_rabbit), \
         patch("bot.handlers._get_user_language", new_callable=AsyncMock, return_value="EN"), \
         patch("bot.handlers.enforce_rate_limit", new_callable=AsyncMock, return_value=True):
        await force_refresh_command(update, context)

    mock_rabbit.publish_message.assert_called_once()
    published_msg = mock_rabbit.publish_message.call_args[0][0]
    assert published_msg["number"] == "12345"
    assert published_msg["type"] == "TP"
    assert published_msg["force_refresh"] is True
    assert "source" not in published_msg


@pytest.mark.asyncio
async def test_force_refresh_command_single_zov_subscription():
    """force_refresh with one ZOV subscription must include source='zov' in published message"""
    zov_sub = make_zov_subscription()

    mock_db = AsyncMock()
    mock_db.fetch_user_subscriptions = AsyncMock(return_value=[zov_sub])
    mock_rabbit = AsyncMock()

    update = Mock()
    update.edited_message = None
    update.message = AsyncMock()
    update.message.text = "/force_refresh"
    update.message.reply_text = AsyncMock()
    update.effective_chat.id = 200
    context = Mock()
    context.user_data = {}

    with patch("bot.handlers.db", mock_db), \
         patch("bot.handlers.rabbit", mock_rabbit), \
         patch("bot.handlers._get_user_language", new_callable=AsyncMock, return_value="EN"), \
         patch("bot.handlers.enforce_rate_limit", new_callable=AsyncMock, return_value=True):
        await force_refresh_command(update, context)

    mock_rabbit.publish_message.assert_called_once()
    published_msg = mock_rabbit.publish_message.call_args[0][0]
    assert published_msg["number"] == "ISTA202504220001"
    assert published_msg["force_refresh"] is True
    assert "source" in published_msg, "Force refresh message for ZOV must include 'source'"
    assert published_msg["source"] == "zov"


@pytest.mark.asyncio
async def test_force_refresh_button_zov():
    """force_refresh via button for ZOV must include source='zov' in published message"""
    mock_rabbit = AsyncMock()

    update = Mock()
    update.callback_query = AsyncMock()
    update.callback_query.data = "force_refresh_ISTA202504220001-ZOV-0"
    update.callback_query.message = AsyncMock()
    update.callback_query.message.reply_text = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.effective_chat.id = 200
    context = Mock()
    context.user_data = {}

    with patch("bot.handlers.rabbit", mock_rabbit), \
         patch("bot.handlers._get_user_language", new_callable=AsyncMock, return_value="EN"), \
         patch("bot.handlers._is_button_click_abused", new_callable=AsyncMock, return_value=False):
        await force_refresh_button(update, context)

    mock_rabbit.publish_message.assert_called_once()
    published_msg = mock_rabbit.publish_message.call_args[0][0]
    assert published_msg["number"] == "ISTA202504220001"
    assert published_msg["force_refresh"] is True
    assert "source" in published_msg, "Force refresh button message for ZOV must include 'source'"
    assert published_msg["source"] == "zov"


# ---------------------------------------------------------------------------
# Commands: unsubscribe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unsubscribe_command_single_oam():
    """unsubscribe with one OAM subscription auto-deletes and shows correct label"""
    mock_db = AsyncMock()
    oam_sub = {
        "application_number": "12345",
        "application_suffix": "0",
        "application_type": "TP",
        "application_year": 2023,
    }
    mock_db.fetch_user_subscriptions = AsyncMock(return_value=[oam_sub])
    mock_db.delete_application = AsyncMock(return_value=True)

    update = Mock()
    update.edited_message = None
    update.message.chat_id = 100
    update.message.reply_text = AsyncMock()
    update.effective_chat.id = 100
    update.effective_chat.username = "testuser"
    update.effective_chat.first_name = "Test"
    update.effective_chat.last_name = "User"

    context = Mock()
    context.user_data = {}

    with patch("bot.handlers.db", mock_db), \
         patch("bot.handlers._get_user_language", new_callable=AsyncMock, return_value="EN"):
        await unsubscribe_command(update, context)

    mock_db.delete_application.assert_called_once_with(100, "12345", "TP", 2023)
    reply_text = update.message.reply_text.call_args[0][0]
    assert "OAM-12345/TP-2023" in reply_text


@pytest.mark.asyncio
async def test_unsubscribe_button_zov_label():
    """unsubscribe button for ZOV must show ZOV number, not OAM format"""
    mock_db = AsyncMock()
    mock_db.delete_application = AsyncMock(return_value=True)

    update = Mock()
    update.callback_query = AsyncMock()
    update.callback_query.data = "unsubscribe_ISTA202504220001-ZOV-0"
    update.callback_query.edit_message_text = AsyncMock()
    update.effective_chat.id = 200
    context = Mock()
    context.user_data = {}

    with patch("bot.handlers.db", mock_db), \
         patch("bot.handlers._get_user_language", new_callable=AsyncMock, return_value="EN"), \
         patch("bot.handlers._is_button_click_abused", new_callable=AsyncMock, return_value=False):
        await unsubscribe_button(update, context)

    call_args = update.callback_query.edit_message_text.call_args[0][0]
    assert "ISTA202504220001" in call_args
    assert "OAM" not in call_args, "ZOV unsubscribe message must not show OAM prefix"


# ---------------------------------------------------------------------------
# Commands: fetcher_stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetcher_stats_command_shows_hard_failures_and_freshness():
    """Fetcher stats keep Telegram admin view but align failure wording"""
    update = Mock()
    update.effective_chat.id = 1234567
    update.effective_chat.username = "admin"
    update.effective_chat.first_name = "Admin"
    update.effective_chat.last_name = "User"
    update.message.reply_text = AsyncMock()
    context = Mock()

    rabbit_mock = Mock()
    rabbit_mock.fetcher_stats.get_all_fetcher_metrics = AsyncMock(
        return_value={
            "fetcher-1": {
                "connection_status": "✅ Connected",
                "average_latency": 0.42,
                "fetch_status": {"success": 5, "failed": 2, "retries": 3},
                "request_state": {"waiting": 1, "locked": 0},
                "rates": {"success_rate": 1.0, "failure_rate": 0.4, "retry_rate": 0.6},
                "rate_interval": 600,
                "ttl": 1800,
                "uptime": 3660,
                "version": "v2.3.3-test",
                "reported_at": 988,
            }
        }
    )

    with patch("bot.handlers.rabbit", rabbit_mock), \
         patch("bot.handlers.time.time", return_value=1000):
        await fetcher_stats_command(update, context)

    text = update.message.reply_text.call_args.args[0]
    assert "Hard failures" in text
    assert "Hard failure rate" in text
    assert "Last report" in text
    assert "12s</b> ago" in text


# ---------------------------------------------------------------------------
# Commands: admin_broadcast
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_broadcast_command_unauthorized():
    """Non-admin user is rejected"""
    update = Mock()
    update.effective_chat.id = 999999
    update.effective_chat.username = "nobody"
    update.effective_chat.first_name = "No"
    update.effective_chat.last_name = "Body"
    update.message.reply_text = AsyncMock()

    context = Mock()
    result = await admin_broadcast_command(update, context)

    update.message.reply_text.assert_called_once_with("Unauthorized. This command is only for admins.")
    from telegram.ext import ConversationHandler
    assert result == ConversationHandler.END


@pytest.mark.asyncio
async def test_admin_broadcast_command_authorized():
    """Admin user gets the broadcast prompt"""
    update = Mock()
    update.effective_chat.id = 1234567  # matches ADMIN_CHAT_IDS in conftest
    update.effective_chat.username = "admin"
    update.effective_chat.first_name = "Admin"
    update.effective_chat.last_name = "User"
    update.message.reply_text = AsyncMock()

    context = Mock()
    result = await admin_broadcast_command(update, context)

    assert result == BROADCAST_TEXT
    update.message.reply_text.assert_called_once()


@pytest.mark.asyncio
async def test_admin_broadcast_text_stores_message():
    """Broadcast text step stores message and shows confirmation with user count"""
    mock_db = AsyncMock()
    mock_db.count_users_total = AsyncMock(return_value=42)

    update = Mock()
    update.message.text = "Hello everyone!"
    update.message.reply_text = AsyncMock()

    context = Mock()
    context.user_data = {}

    with patch("bot.handlers.db", mock_db):
        result = await admin_broadcast_text(update, context)

    assert result == BROADCAST_CONFIRM
    assert context.user_data["broadcast_message"] == "Hello everyone!"
    call_text = update.message.reply_text.call_args[0][0]
    assert "42" in call_text
    assert "Hello everyone!" in call_text


@pytest.mark.asyncio
async def test_admin_broadcast_confirm_starts_background_task():
    """Confirm broadcast responds immediately and fires async task"""
    mock_db = AsyncMock()
    mock_db.fetch_all_chat_ids = AsyncMock(return_value=[100, 200, 300])

    update = Mock()
    update.callback_query = AsyncMock()
    update.callback_query.data = "confirm_broadcast"
    update.callback_query.message.chat_id = 1234567
    update.callback_query.edit_message_text = AsyncMock()
    update.effective_chat.id = 1234567
    update.effective_chat.username = "admin"
    update.effective_chat.first_name = "Admin"
    update.effective_chat.last_name = "User"

    context = Mock()
    context.user_data = {"broadcast_message": "Test broadcast"}
    context.bot = AsyncMock()

    with patch("bot.handlers.db", mock_db), \
         patch("bot.handlers.asyncio.create_task") as mock_create_task:
        result = await admin_broadcast_confirm(update, context)

    from telegram.ext import ConversationHandler
    assert result == ConversationHandler.END
    # Responded immediately with "started" message
    edit_text = update.callback_query.edit_message_text.call_args[0][0]
    assert "3 users" in edit_text
    assert "started" in edit_text.lower()
    # Background task was fired
    mock_create_task.assert_called_once()
    # Context cleaned up
    assert "broadcast_message" not in context.user_data


@pytest.mark.asyncio
async def test_admin_broadcast_cancel():
    """Cancel broadcast shows cancel message and cleans up"""
    update = Mock()
    update.callback_query = AsyncMock()
    update.callback_query.data = "cancel_broadcast"
    update.callback_query.edit_message_text = AsyncMock()
    update.effective_chat.id = 1234567
    update.effective_chat.username = "admin"
    update.effective_chat.first_name = "Admin"
    update.effective_chat.last_name = "User"

    context = Mock()
    context.user_data = {"broadcast_message": "Test broadcast"}

    with patch("bot.handlers.db", AsyncMock()):
        result = await admin_broadcast_confirm(update, context)

    from telegram.ext import ConversationHandler
    assert result == ConversationHandler.END
    update.callback_query.edit_message_text.assert_called_once_with("Broadcast canceled.")
    assert "broadcast_message" not in context.user_data


@pytest.mark.asyncio
async def test_broadcast_to_users_counts_success_and_failure():
    """Background broadcast counts delivered and failed messages"""
    bot = AsyncMock()
    # First two succeed, third raises Forbidden (blocked)
    bot.send_message = AsyncMock(
        side_effect=[None, None, Exception("Forbidden: bot was blocked by the user")]
    )

    await _broadcast_to_users(bot, admin_chat_id=1234567, chat_ids=[100, 200, 300], message="Hi")

    # 3 user sends + 1 summary to admin = 4 calls
    assert bot.send_message.call_count == 4
    # Last call is the summary to admin
    summary_text = bot.send_message.call_args_list[-1].args[1]
    assert "2 delivered" in summary_text
    assert "1 failed" in summary_text
    assert "3 total" in summary_text


@pytest.mark.asyncio
async def test_broadcast_to_users_retries_on_timeout():
    """Broadcast retries on TimedOut and eventually succeeds"""
    from telegram.error import TimedOut

    bot = AsyncMock()
    bot.send_message = AsyncMock(
        side_effect=[TimedOut(), None, None]  # first call times out, retry succeeds; then summary
    )

    await _broadcast_to_users(bot, admin_chat_id=999, chat_ids=[100], message="Hi")

    # 2 attempts for user + 1 summary = 3 calls
    assert bot.send_message.call_count == 3
    summary_text = bot.send_message.call_args_list[-1].args[1]
    assert "1 delivered" in summary_text
    assert "0 failed" in summary_text


@pytest.mark.asyncio
async def test_broadcast_to_users_retries_exhausted():
    """Broadcast gives up after max retries and counts as failed"""
    from telegram.error import TimedOut

    bot = AsyncMock()
    # 3 timeouts for user, then summary send
    bot.send_message = AsyncMock(
        side_effect=[TimedOut(), TimedOut(), TimedOut(), None]
    )

    await _broadcast_to_users(bot, admin_chat_id=999, chat_ids=[100], message="Hi")

    summary_text = bot.send_message.call_args_list[-1].args[1]
    assert "0 delivered" in summary_text
    assert "1 failed" in summary_text


@pytest.mark.asyncio
async def test_broadcast_marks_dead_user_inactive():
    """A real Forbidden('bot was blocked by the user') flips the user inactive
    and stops retrying — single attempt only
    """
    from telegram.error import Forbidden

    bot = AsyncMock()
    bot.send_message = AsyncMock(
        side_effect=[Forbidden("Forbidden: bot was blocked by the user"), None]
    )
    db_mock = AsyncMock()
    db_mock.mark_user_inactive = AsyncMock(return_value=True)

    with patch("bot.handlers.db", db_mock):
        await _broadcast_to_users(bot, admin_chat_id=999, chat_ids=[100], message="Hi")

    db_mock.mark_user_inactive.assert_awaited_once()
    assert db_mock.mark_user_inactive.call_args[0][0] == 100
    # exactly 1 user attempt + 1 admin summary = 2 sends (no retries on terminal)
    assert bot.send_message.call_count == 2
    summary_text = bot.send_message.call_args_list[-1].args[1]
    assert "1 failed" in summary_text


@pytest.mark.asyncio
async def test_broadcast_does_not_mark_inactive_on_non_dead_badrequest():
    """A BadRequest that is NOT in the dead-user hint list (e.g. message too long)
    must not flip the user inactive — it's a permanent_other error, not a dead chat
    """
    from telegram.error import BadRequest

    bot = AsyncMock()
    bot.send_message = AsyncMock(
        side_effect=[BadRequest("Bad Request: message is too long"), None]
    )
    db_mock = AsyncMock()
    db_mock.mark_user_inactive = AsyncMock()

    with patch("bot.handlers.db", db_mock):
        await _broadcast_to_users(bot, admin_chat_id=999, chat_ids=[100], message="Hi")

    db_mock.mark_user_inactive.assert_not_called()
