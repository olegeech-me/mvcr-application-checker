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


def generate_oam_full_string(app_details):
    """Generate full application identifier (OAM or ZOV)"""

    source = (app_details.get("source")
              or app_details.get("application_source", "oam"))

    if source == "zov":
        return app_details.get("number") or app_details.get("application_number")

    # Extract data, checking for both possible key formats
    number = app_details.get("number") or app_details.get("application_number")
    suffix = app_details.get("suffix") or app_details.get("application_suffix", "0")
    type_ = app_details.get("type") or app_details.get("application_type")
    year = app_details.get("year") or app_details.get("application_year")

    if suffix != "0":
        return "OAM-{}-{}/{}-{}".format(number, suffix, type_, year)
    return "OAM-{}/{}-{}".format(number, type_, year)


def categorize_application_status(status):
    """Return category and emoji based on status string"""
    for category, (keywords, emoji_sign) in MVCR_STATUSES.items():
        for keyword in keywords:
            if keyword in status:
                return category, emoji_sign
    logger.error(f"Failed to categorize status: {status}")
    return None, None


async def notify_user(bot, chat_id, text, max_retries=5):
    """Notify user with retries on intermittent issues"""
    attempt = 0
    delay = 1
    while attempt < max_retries:
        try:
            await bot.updater.bot.send_message(chat_id=chat_id, text=text)
            logger.debug(f"Sent status update to chatID {chat_id}")
            return
        except RetryAfter as e:
            delay = e.retry_after
            logger.warning(f"RetryAfter: failed to notify chat_id {chat_id}: retrying after {delay} seconds")
        except TimedOut:
            logger.warning(f"TimedOut: failed to notify chat_id {chat_id}: retrying after {delay} seconds")
        except NetworkError:
            logger.warning(f"NetworkError: failed to notify chat_id {chat_id}: retrying after {delay} seconds")
        except Exception as e:
            logger.error(f"Failed to send status update to {chat_id}: {e}")
            return

        await asyncio.sleep(delay)
        attempt += 1
        delay *= 2  # exponential retry increase

    logger.error(f"Failed to send message to {chat_id} after {max_retries} attempts")
