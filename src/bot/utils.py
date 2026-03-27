import asyncio
import logging


# https://docs.python-telegram-bot.org/en/v20.5/telegram.error.html
from telegram.error import NetworkError, TimedOut, RetryAfter

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


async def notify_user(bot, chat_id, text, max_retries=5, username=None, first_name=None, last_name=None):
    """Notify user with retries on intermittent issues"""
    label = user_label(chat_id, username, first_name, last_name)
    attempt = 0
    delay = 1
    while attempt < max_retries:
        try:
            await bot.updater.bot.send_message(chat_id=chat_id, text=text)
            logger.debug(f"Sent status update to {label}")
            return
        except RetryAfter as e:
            delay = e.retry_after
            logger.warning(f"RetryAfter: failed to notify {label}: retrying after {delay} seconds")
        except TimedOut:
            logger.warning(f"TimedOut: failed to notify {label}: retrying after {delay} seconds")
        except NetworkError:
            logger.warning(f"NetworkError: failed to notify {label}: retrying after {delay} seconds")
        except Exception as e:
            logger.error(f"Failed to send status update to {label}: {e}")
            return

        await asyncio.sleep(delay)
        attempt += 1
        delay *= 2  # exponential retry increase

    logger.error(f"Failed to send message to {label} after {max_retries} attempts")
