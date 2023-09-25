from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
import logging
import time
from bot.loader import rabbit, db, ADMIN_CHAT_ID

BUTTON_WAIT_SECONDS = 5

subscribe_helper_text = """
Please run command /subscribe with the following arguments:
- application number (usually 4 to 5 digits)
- application suffix (put 0, if you don't have it)
- application type (TP,DP,MK, etc...)
- year of application (4 digits)

Example: /subscribe 12345 0 TP 2023

"""

logger = logging.getLogger(__name__)


def _is_admin(chat_id: int) -> bool:
    """Check if the user's chat_id is an admin's chat_id."""
    logger.info(f"Effective chat_id: '{chat_id}', Allowed admin id: '{ADMIN_CHAT_ID}'")
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
    await update.message.reply_text("Thank you for using the MVČR application status bot!")
    keyboard = [
        [InlineKeyboardButton("Subscribe", callback_data="subscribe")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Please hit the button below to subscribe for your application status updates.", reply_markup=reply_markup
    )


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
            await query.edit_message_text(subscribe_helper_text)


# Handler for the /help command
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays info on how to use the bot."""
    await update.message.reply_text("Press bot menu for this list of available commands.")


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
            await message.reply_text(subscribe_helper_text)
    except Exception as e:
        await message.reply_text(f"An error occurred: {e}")
