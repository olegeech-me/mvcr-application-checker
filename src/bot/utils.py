import asyncio
import logging


# https://docs.python-telegram-bot.org/en/v20.5/telegram.error.html
# Order matters: BadRequest is a subclass of NetworkError in PTB v20.5,
# so it MUST be caught before NetworkError or terminal 400s (e.g. "chat not found")
# get treated as transient and silently retried forever
from telegram.error import (
    Forbidden, BadRequest, RetryAfter, TimedOut, NetworkError, ChatMigrated,
)

logger = logging.getLogger(__name__)

MVCR_STATUSES = {
    "not_found": (["nebylo nalezeno", "bez úvodních nul", "not found"], "⚪️"),
    "in_progress": (["zpracovává se", "v-prubehu-rizeni", "being processed"], "🟡"),
    "pre_approved": (["preliminarily assessed positively",
                      "předběžně vyhodnoceno kladně"], "⭐"),
    "approved": (["bylo <b>povoleno</b>", "rizeni-povoleno"], "🟢"),
    "denied": (["bylo <b>nepovoleno</b>", "zamítlo", "zastavilo",
                "<b>rejected</b>", "have been closed"], "🔴"),
    "suspended": (["přerušeno", "has been suspended"], "🟠"),
    "error": (["ERROR"], "🔴"),
}

# Substrings (case-insensitive) inside a Forbidden / BadRequest message
# that indicate the user is permanently unreachable. Anything else is
# treated as "permanent_other" and the row is consumed without retry but
# the user is NOT marked inactive
DEAD_USER_FORBIDDEN_HINTS = (
    "bot was blocked by the user",
    "user is deactivated",
    "bot can't initiate conversation with a user",
)
DEAD_USER_BADREQUEST_HINTS = (
    "chat not found",
    "user not found",
    "peer_id_invalid",
)


def user_label_short(chat_id, username=None, first_name=None, **_):
    """Compact user identifier for routine system log lines."""
    if username:
        return f"@{username}"
    if first_name:
        return first_name
    return str(chat_id)


def _get(d, short_key, default=None):
    """Look up a key in both short ('number') and DB ('application_number') formats."""
    return d.get(short_key) or d.get(f"application_{short_key}", default)


def generate_oam_full_string(app_details):
    """Generate full application identifier (OAM or ZOV)"""
    if _get(app_details, "type") == "ZOV":
        return _get(app_details, "number")

    number = _get(app_details, "number")
    suffix = _get(app_details, "suffix", "0")
    type_ = _get(app_details, "type")
    year = _get(app_details, "year")

    if suffix != "0":
        return f"OAM-{number}-{suffix}/{type_}-{year}"
    return f"OAM-{number}/{type_}-{year}"


def user_label(chat_id, username=None, first_name=None, last_name=None):
    """Format user identity for log messages.

    Produces a uniform key-value string with human-readable fields first
    and chat_id always at the end, e.g.:
      first_name: Vasya, last_name: Pupkin, username: vasya123, chat_id: 123
    """
    pieces = []
    if first_name:
        pieces.append(f"first_name: {first_name}")
    if last_name:
        pieces.append(f"last_name: {last_name}")
    if username:
        pieces.append(f"username: {username}")
    pieces.append(f"chat_id: {chat_id}")
    return ", ".join(pieces)


def categorize_application_status(status):
    """Return category and emoji based on status string"""
    for category, (keywords, emoji_sign) in MVCR_STATUSES.items():
        for keyword in keywords:
            if keyword in status:
                return category, emoji_sign
    logger.error(f"Failed to categorize status: {status}")
    return None, None


def classify_send_error(exc):
    """Map a Telegram permanent exception to a delivery verdict

    Returns:
      "dead_user"       — chat permanently unreachable; mark the user inactive
      "permanent_other" — terminal but not a known dead-user signal; log and
                          stop retrying

    Caller is expected to pass only Forbidden / BadRequest / ChatMigrated
    instances. NetworkError / TimedOut / RetryAfter belong to the retry path
    and must not be funnelled in here
    """
    msg = (getattr(exc, "message", None) or str(exc) or "").lower()
    if isinstance(exc, Forbidden):
        if any(hint in msg for hint in DEAD_USER_FORBIDDEN_HINTS):
            return "dead_user"
        return "permanent_other"
    if isinstance(exc, BadRequest):
        if any(hint in msg for hint in DEAD_USER_BADREQUEST_HINTS):
            return "dead_user"
        return "permanent_other"
    if isinstance(exc, ChatMigrated):
        # 1:1 chats don't migrate; if we ever see this, treat as terminal
        return "permanent_other"
    raise NotImplementedError(
        f"classify_send_error called with unsupported exception: {type(exc).__name__}"
    )


async def notify_user(bot, chat_id, text, max_retries=5, username=None, first_name=None, last_name=None):
    """Send a message to the user, retrying on intermittent issues

    Returns one of:
      "ok"                — delivered
      "dead_user"         — chat permanently unreachable (Forbidden / BadRequest
                            matching the dead-user substring tables)
      "permanent_other"   — terminal Telegram error, not a known dead-user signal;
                            don't retry, don't mark the user inactive
      "retryable_gave_up" — exhausted max_retries against a transient error
                            (TimedOut / NetworkError / RetryAfter)
    """
    label = user_label(chat_id, username, first_name, last_name)
    attempt = 0
    delay = 1
    while attempt < max_retries:
        try:
            await bot.updater.bot.send_message(chat_id=chat_id, text=text)
            logger.debug(f"Sent message to {label}")
            return "ok"
        except RetryAfter as e:
            delay = e.retry_after
            logger.warning(f"RetryAfter: failed to notify {label}: retrying after {delay} seconds")
        except (Forbidden, BadRequest, ChatMigrated) as e:
            verdict = classify_send_error(e)
            logger.warning(
                f"{type(e).__name__}: terminal send error for {label}, verdict={verdict}: {e}"
            )
            return verdict
        except TimedOut:
            logger.warning(f"TimedOut: failed to notify {label}: retrying after {delay} seconds")
        except NetworkError:
            logger.warning(f"NetworkError: failed to notify {label}: retrying after {delay} seconds")
        except Exception as e:
            logger.error(f"Unexpected error sending message to {label}: {e!r}")
            return "permanent_other"

        await asyncio.sleep(delay)
        attempt += 1
        delay *= 2  # exponential retry increase

    logger.error(f"Failed to send message to {label} after {max_retries} attempts")
    return "retryable_gave_up"
