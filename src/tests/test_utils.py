from unittest.mock import AsyncMock, Mock, patch

import pytest
from conftest import make_rabbit
from telegram.error import (
    BadRequest,
    ChatMigrated,
    Forbidden,
    NetworkError,
    RetryAfter,
    TimedOut,
)

from bot.utils import (
    MVCR_STATUSES,
    categorize_application_status,
    classify_send_error,
    generate_oam_full_string,
    notify_user,
    user_label,
    user_label_short,
)

# ---------------------------------------------------------------------------
# generate_oam_full_string
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "app_details, expected",
    [
        # OAM with short keys (RabbitMQ message format)
        ({"number": "4242", "suffix": "0", "type": "TP", "year": "2042"}, "OAM-4242/TP-2042"),
        # OAM with suffix
        ({"number": "4242", "suffix": "5", "type": "DO", "year": "2020"}, "OAM-4242-5/DO-2020"),
        # OAM with DB column keys
        (
            {"application_number": "12345", "application_suffix": "0", "application_type": "MK", "application_year": "2023"},
            "OAM-12345/MK-2023",
        ),
        # ZOV with short keys
        ({"number": "ISTA202504220001", "type": "ZOV", "year": 0}, "ISTA202504220001"),
        # ZOV with DB column keys
        (
            {
                "application_number": "ISTA202601150003",
                "application_type": "ZOV",
                "application_year": 0,
                "application_suffix": "0",
            },
            "ISTA202601150003",
        ),
        # No type=ZOV defaults to OAM
        ({"number": "999", "suffix": "0", "type": "TP", "year": "2025"}, "OAM-999/TP-2025"),
    ],
)
def test_generate_oam_full_string(app_details, expected):
    assert generate_oam_full_string(app_details) == expected


# ---------------------------------------------------------------------------
# categorize_application_status
# ---------------------------------------------------------------------------


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


def test_categorize_zov_pre_approved_with_povoleno_link():
    """Real ZOV pre_approved response contains 'rizeni-povoleno' in a link URL;
    must still be classified as pre_approved, not approved"""
    real_status = (
        'Číslo žádosti o vízum<strong> ISTA202504220001 </strong>bylo '
        '<b>předběžně vyhodnoceno kladně</b>. \n\nPro objednání a případné další '
        'informace kontaktujte <a href="https://ipc.gov.cz/kontakty/#3">klientské '
        'centrum</a> na čísle +420 974 801 801 (Po-Čt 8:00-16:00, Pá 8:00-14:00). '
        'Informace o tom, jak dále postupovat, naleznete dále na '
        '<a href="https://ipc.gov.cz/spravni-rizeni/rizeni-povoleno/">této stránce</a>.'
        '\n\n<b>Stav řízení je pouze orientační.</b>'
    )
    category, emoji = categorize_application_status(real_status)
    assert category == "pre_approved", (
        f"Expected pre_approved but got {category}; "
        f"'rizeni-povoleno' in link URL must not trigger approved"
    )
    assert emoji == "⭐"


# ---------------------------------------------------------------------------
# user_label
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "chat_id, username, first_name, last_name, expected",
    [
        (123, "vasya123", "Vasya", "Pupkin",
         "first_name: Vasya, last_name: Pupkin, username: vasya123, chat_id: 123"),
        (123, "vasya123", "Vasya", None,
         "first_name: Vasya, username: vasya123, chat_id: 123"),
        (123, None, "Vasya", "Pupkin",
         "first_name: Vasya, last_name: Pupkin, chat_id: 123"),
        (123, None, "Vasya", None,
         "first_name: Vasya, chat_id: 123"),
        (123, "vasya123", None, None,
         "username: vasya123, chat_id: 123"),
        (123, None, None, None,
         "chat_id: 123"),
        (123, "vasya123", None, "Pupkin",
         "last_name: Pupkin, username: vasya123, chat_id: 123"),
    ],
)
def test_user_label(chat_id, username, first_name, last_name, expected):
    assert user_label(chat_id, username, first_name, last_name) == expected


def test_user_label_empty_strings_treated_as_missing():
    """Empty strings should be treated same as None (omitted)"""
    assert user_label(123, "", "", "") == "chat_id: 123"


# ---------------------------------------------------------------------------
# user_label_short
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "chat_id, username, first_name, expected",
    [
        (123, "vasya123", "Vasya", "@vasya123"),
        (123, None, "Vasya", "Vasya"),
        (123, None, None, "123"),
        (123, "", "Vasya", "Vasya"),
        (123, "", "", "123"),
    ],
)
def test_user_label_short(chat_id, username, first_name, expected):
    assert user_label_short(chat_id, username, first_name) == expected


def test_user_label_short_ignores_extra_kwargs():
    """Extra kwargs (e.g. last_name) are silently ignored"""
    assert user_label_short(123, username="vasya", first_name="V", last_name="P") == "@vasya"


# ---------------------------------------------------------------------------
# pre_approved resolution check
# ---------------------------------------------------------------------------


def test_pre_approved_in_resolved_statuses():
    """pre_approved IS a final/resolved status (ZOV has no separate approved)"""
    rabbit = make_rabbit()
    for kw in MVCR_STATUSES.get("pre_approved")[0]:
        assert rabbit.processor._is_resolved(f"Application {kw}"), f"'{kw}' must be treated as resolved"


# ---------------------------------------------------------------------------
# i18n
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# classify_send_error
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc, expected",
    [
        # Forbidden — dead-user signals
        (Forbidden("Forbidden: bot was blocked by the user"), "dead_user"),
        (Forbidden("Forbidden: user is deactivated"), "dead_user"),
        (Forbidden("Forbidden: bot can't initiate conversation with a user"), "dead_user"),
        # Forbidden — anything else is permanent_other (e.g. group-chat restrictions)
        (Forbidden("Forbidden: bot is not a member of the group chat"), "permanent_other"),
        # BadRequest — dead-user signals
        (BadRequest("Bad Request: chat not found"), "dead_user"),
        (BadRequest("Bad Request: user not found"), "dead_user"),
        (BadRequest("Bad Request: PEER_ID_INVALID"), "dead_user"),
        # BadRequest — anything else (formatting, parse errors, ...) is permanent_other
        (BadRequest("Bad Request: message is too long"), "permanent_other"),
        (BadRequest("Bad Request: can't parse entities"), "permanent_other"),
        # ChatMigrated — terminal but not a dead-user signal in 1:1 chats
        (ChatMigrated(new_chat_id=-1009999999999), "permanent_other"),
    ],
)
def test_classify_send_error(exc, expected):
    assert classify_send_error(exc) == expected


def test_classify_send_error_rejects_unsupported_types():
    """Transient errors must NOT be funnelled into classify_send_error;
    they belong on the retry path
    """
    with pytest.raises(NotImplementedError):
        classify_send_error(NetworkError("temporary glitch"))
    with pytest.raises(NotImplementedError):
        classify_send_error(TimedOut())
    with pytest.raises(NotImplementedError):
        classify_send_error(RetryAfter(retry_after=10))


def test_classify_send_error_badrequest_is_subclass_of_networkerror():
    """Regression guard: PTB v20.5 makes BadRequest a NetworkError subclass.
    A naive `except (TimedOut, NetworkError)` would silently swallow terminal
    400 responses and retry forever. Lock down the hierarchy assumption.
    """
    assert issubclass(BadRequest, NetworkError)


# ---------------------------------------------------------------------------
# notify_user verdict strings
# ---------------------------------------------------------------------------


def _make_bot_returning(side_effect):
    bot = Mock()
    bot.updater = Mock()
    bot.updater.bot = Mock()
    bot.updater.bot.send_message = AsyncMock(side_effect=side_effect)
    return bot


@pytest.mark.asyncio
async def test_notify_user_ok():
    bot = _make_bot_returning(None)
    with patch("bot.utils.prometheus_metrics.set_telegram_last_ok") as set_ok:
        assert await notify_user(bot, 123, "hi") == "ok"
    bot.updater.bot.send_message.assert_awaited_once()
    set_ok.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc, expected",
    [
        (Forbidden("Forbidden: bot was blocked by the user"), "dead_user"),
        (BadRequest("Bad Request: chat not found"), "dead_user"),
        (Forbidden("Forbidden: bot is not a member of the supergroup chat"), "permanent_other"),
        (BadRequest("Bad Request: message is too long"), "permanent_other"),
    ],
)
async def test_notify_user_terminal_returns_verdict_without_retry(exc, expected):
    bot = _make_bot_returning(exc)
    assert await notify_user(bot, 123, "hi", max_retries=5) == expected
    # terminal errors must NOT be retried
    bot.updater.bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_notify_user_retryable_gave_up_after_max_retries(monkeypatch):
    """Persistent transient errors exhaust max_retries and return retryable_gave_up
    (this is the verdict the NotificationMonitor will retry later)
    """
    monkeypatch.setattr("bot.utils.asyncio.sleep", AsyncMock())

    bot = _make_bot_returning(TimedOut())
    with patch("bot.utils.prometheus_metrics.record_error") as record_error:
        assert await notify_user(bot, 123, "hi", max_retries=3) == "retryable_gave_up"
    assert bot.updater.bot.send_message.await_count == 3
    assert record_error.call_count == 3
    record_error.assert_called_with("telegram", "timeout")


@pytest.mark.asyncio
async def test_notify_user_network_error_records_telegram_network_metric(monkeypatch):
    monkeypatch.setattr("bot.utils.asyncio.sleep", AsyncMock())

    bot = _make_bot_returning(NetworkError("connect failed"))
    with patch("bot.utils.prometheus_metrics.record_error") as record_error:
        assert await notify_user(bot, 123, "hi", max_retries=2) == "retryable_gave_up"
    assert record_error.call_count == 2
    record_error.assert_called_with("telegram", "network")


@pytest.mark.asyncio
async def test_notify_user_unknown_exception_returns_permanent_other(monkeypatch):
    """A non-Telegram exception is treated as terminal (permanent_other) — the
    monitor will not retry it and it will not flag the user as dead
    """
    monkeypatch.setattr("bot.utils.asyncio.sleep", AsyncMock())

    bot = _make_bot_returning(RuntimeError("kaboom"))
    assert await notify_user(bot, 123, "hi", max_retries=3) == "permanent_other"
    bot.updater.bot.send_message.assert_awaited_once()


# ---------------------------------------------------------------------------
# i18n
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lang", ["EN", "RU", "CZ", "UA"])
def test_i18n_has_zov_keys(lang):
    """All languages must have ZOV-related i18n keys with proper content"""
    from bot.texts import message_texts as mt

    assert "pre_approved" in mt[lang], f"Missing 'pre_approved' in {lang}"
    assert "{status_sign}" in mt[lang]["pre_approved"], f"'pre_approved' in {lang} missing {{status_sign}} placeholder"

    assert "dialog_confirmation_zov" in mt[lang], f"Missing 'dialog_confirmation_zov' in {lang}"
    assert "{number}" in mt[lang]["dialog_confirmation_zov"], f"'dialog_confirmation_zov' in {lang} missing {{number}} placeholder"
    assert "OAM" not in mt[lang]["dialog_confirmation_zov"], f"'dialog_confirmation_zov' in {lang} should not contain 'OAM'"

    assert "dialog_source" in mt[lang], f"Missing 'dialog_source' in {lang}"
    assert "dialog_app_number_oam" in mt[lang], f"Missing 'dialog_app_number_oam' in {lang}"
    assert "dialog_app_number_zov" in mt[lang], f"Missing 'dialog_app_number_zov' in {lang}"
    assert "ISTA" in mt[lang]["dialog_app_number_zov"], f"'dialog_app_number_zov' in {lang} should mention ZOV example number"
    assert "error_invalid_number_zov" in mt[lang], f"Missing 'error_invalid_number_zov' in {lang}"
