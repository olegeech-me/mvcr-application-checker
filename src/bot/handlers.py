from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
import logging
import time
from bot.loader import rabbit, db, ADMIN_CHAT_ID, REFRESH_PERIOD
from bot.texts import button_texts, message_texts

BUTTON_WAIT_SECONDS = 5
FORCE_FETCH_LIMIT_SECONDS = 86400

logger = logging.getLogger(__name__)


def _is_admin(chat_id: int) -> bool:
    """Check if the user's chat_id is an admin's chat_id."""
    logger.debug(f"Effective chat_id: '{chat_id}', Allowed admin id: '{ADMIN_CHAT_ID}'")
    return int(chat_id) == int(ADMIN_CHAT_ID)


def user_info(update: Update):
    """Returns a string with user information"""
    chatid = update.effective_chat.id
    username = update.effective_chat.username
    first_name = update.effective_chat.first_name
    last_name = update.effective_chat.last_name
    info_pieces = [f"chat_id: {chatid}"]
    if username:
        info_pieces.append(f"username: {username}")
    if first_name:
        info_pieces.append(f"first_name: {first_name}")
    if last_name:
        info_pieces.append(f"last_name: {last_name}")

    return ", ".join(info_pieces)


def get_effective_message(update: Update):
    """
    Returns the effective message from the update, regardless of whether it's
    a new or edited message.
    """
    return update.edited_message or update.message


def check_and_update_limit(user_data, command_name):
    """Verifies and limits how many times a command was used a day"""
    current_time = time.time()
    key_name = f"{command_name}_timestamps"

    if key_name not in user_data:
        user_data[key_name] = []

    # filter timestamps within the last FORCE_FETCH_LIMIT_SECONDS
    valid_timestamps = [ts for ts in user_data[key_name] if current_time - ts < FORCE_FETCH_LIMIT_SECONDS]

    if len(valid_timestamps) >= 2:
        return False

    valid_timestamps.append(current_time)
    user_data[key_name] = valid_timestamps
    return True


async def enforce_rate_limit(update: Update, context: ContextTypes.DEFAULT_TYPE, command_name: str):
    """
    Enforce rate limits for a given command.
    Returns True if the user is allowed to proceed, False otherwise.
    """
    chat_id = update.effective_chat.id
    # No rate limiting for admin
    if _is_admin(chat_id):
        return True

    # Check rate limit
    if not check_and_update_limit(context.user_data, command_name):
        logger.info(f"Ratelimiting user {chat_id}, command {command_name}")
        await update.message.reply_text("You can only use this command 2 times a day.")
        return False

    return True


# handler for /admin_stats
async def admin_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return total number of subscribed users"""
    logging.info(f"Received /admin_stats command from {user_info(update)}")
    if not _is_admin(update.effective_chat.id):
        await update.message.reply_text("Unauthorized. This command is only for admins.")
        return

    user_count = await db.get_subscribed_user_count()
    if user_count is not None:
        await update.message.reply_text(f"Total users subscribed: {user_count}")
    else:
        await update.message.reply_text("Error retrieving statistics.")


# handler for /admin_restart_fetcher

# hander for /admin_restart_bot

# handler for /admin_oldest_refresh


# Handler for the /start command
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a message with inline buttons attached"""
    logging.info(f"Received /start command from {user_info(update)}")
    await update.message.reply_text(message_texts["start_text"].format(refresh_period=int(REFRESH_PERIOD / 60)))
    keyboard = [
        [InlineKeyboardButton(button_texts["subscribe_button"], callback_data="subscribe")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(message_texts["subscribe_intro"], reply_markup=reply_markup)


# Handler for button clicks
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    current_time = time.time()

    # ignore impatient users spamming buttons
    if "last_button_press" in context.user_data and current_time - context.user_data["last_button_press"] < BUTTON_WAIT_SECONDS:
        await query.answer()
        logger.debug(f"Impatient user ignored: {user_info(update)}")
        return

    context.user_data["last_button_press"] = current_time
    await query.answer()

    if query.data == "subscribe":
        if await db.check_subscription_in_db(query.message.chat_id):
            await query.edit_message_text("You are already subscribed.")
        else:
            await query.edit_message_text(message_texts["subscribe_helper"])


# Handler for the /help command
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays info on how to use the bot."""
    await update.message.reply_text(message_texts["start_text"].format(refresh_period=int(REFRESH_PERIOD / 60)))


# Handler for unknown commands
async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Sorry, I didn't understand that command.")


# Handler for /status command
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Returns current status of the application"""
    logger.info(f"Received /status command from {user_info(update)}")

    if await db.check_subscription_in_db(update.message.chat_id):
        app_status = await db.get_application_status_timestamp(update.message.chat_id)
        await update.message.reply_text(app_status)
    else:
        await update.message.reply_text("You are not subscribed")


# Handler for /unsubscribe command
async def unsubscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unsubscribes user from application status updates"""
    logger.info(f"Received /unsubscribe command from {user_info(update)}")

    if await db.check_subscription_in_db(update.message.chat_id):
        await db.remove_from_db(update.message.chat_id)
        await update.message.reply_text("You have unsubscribed")
    else:
        await update.message.reply_text("You are not subscribed")


# Handler for /subscribe command
async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Subscribes user for application status updates"""
    app_data = context.args
    logger.info(f"Received /subscribe command with args {app_data} from {user_info(update)}")

    message = get_effective_message(update)

    if await db.check_subscription_in_db(message.chat_id):
        await message.reply_text("You are already subscribed")
        return
    if not await enforce_rate_limit(update, context, "subscribe"):
        return
    try:
        if len(app_data) == 4:
            number, suffix, type, year = context.args
            # Input sanitization
            if not number.isdigit():
                await message.reply_text("Invalid application number.")
                return
            if not suffix.isdigit():
                await message.reply_text("Invalid suffix. It should be a number.")
                return
            if len(type) != 2:
                await message.reply_text("Invalid type. It should be two letters (e.g. TP, DP, MK and so on)")
                return
            if not year.isdigit() or len(year) != 4:
                await message.reply_text("Invalid year. It should be 4 digits.")
                return

            request = {
                "chat_id": message.chat_id,
                "number": number,
                "suffix": suffix,
                "type": type.upper(),
                "year": year,
                "last_updated": "0",
            }
            logger.info(f"Received application details {request}")
            # add data to the db
            if await db.add_to_db(
                message.chat_id,
                number,
                suffix,
                type.upper(),
                int(year),
                message.chat.username,
                message.chat.first_name,
                message.chat.last_name,
            ):
                # publish request for fetchers
                await rabbit.publish_message(request)
                await message.reply_text(
                    f"You have been subscribed for application <b>OAM-{number}-{suffix}/{type.upper()}-{year}</b> updates.",
                )
            else:
                await message.reply_text("Failed to subscribe. Please try again later")
        else:
            await message.reply_text(message_texts["subscribe_helper"])
    except Exception as e:
        await message.reply_text(f"An error occurred: {e}")


# Handler for /force_refresh command
async def force_refresh_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force refresh application status."""
    logger.info(f"Received /force_refresh command from {user_info(update)}")
    message = get_effective_message(update)

    if not await db.check_subscription_in_db(message.chat_id):
        await message.reply_text("You are not subscribed.")
        return
    if not await enforce_rate_limit(update, context, "force_refresh"):
        return
    try:
        user_data = await db.get_user_data_from_db(message.chat_id)

        if user_data:
            request = {
                "chat_id": message.chat_id,
                "number": user_data["application_number"],
                "suffix": user_data["application_suffix"],
                "type": user_data["application_type"].upper(),
                "year": user_data["application_year"],
                "force_refresh": True,
                "last_updated": "0",
            }
            logger.info(f"Publishing force refresh for {request}")

            await rabbit.publish_message(request)
            await message.reply_text("Refresh request sent.")
        else:
            await message.reply_text("Failed to retrieve user data. Please try again later.")
    except Exception as e:
        await message.reply_text(f"An error occurred: {e}")
