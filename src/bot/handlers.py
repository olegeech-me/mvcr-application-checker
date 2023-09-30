from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, ConversationHandler
import logging
import time
import datetime
from bot.loader import rabbit, db, ADMIN_CHAT_ID, REFRESH_PERIOD
from bot.texts import button_texts, message_texts

BUTTON_WAIT_SECONDS = 1
FORCE_FETCH_LIMIT_SECONDS = 86400
ALLOWED_TYPES = ["CD", "DO", "DP", "DV", "MK", "PP", "ST", "TP", "VP", "ZK", "ZM"]
POPULAR_ALLOWED_TYPES = ["DP", "TP", "ZM", "MK", "DO"]
ALLOWED_YEARS = [y for y in range(datetime.datetime.today().year - 3, datetime.datetime.today().year + 1)]

START, NUMBER, TYPE, YEAR, VALIDATE = range(5)

logger = logging.getLogger(__name__)


def _is_admin(chat_id: int) -> bool:
    """Check if the user's chat_id is an admin's chat_id"""
    logger.debug(f"Effective chat_id: '{chat_id}', Allowed admin id: '{ADMIN_CHAT_ID}'")
    return int(chat_id) == int(ADMIN_CHAT_ID)


async def _is_button_click_abused(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    current_time = time.time()
    # ignore impatient users spamming buttons
    if "last_button_press" in context.user_data and current_time - context.user_data["last_button_press"] < BUTTON_WAIT_SECONDS:
        await query.answer()
        logger.info(f"Impatient user ignored: {user_info(update)}")
        return True
    context.user_data["last_button_press"] = current_time
    return False


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
    a new or edited message
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
    Enforce rate limits for a given command
    Returns True if the user is allowed to proceed, False otherwise
    """
    chat_id = update.effective_chat.id
    message = update.message or update.callback_query.message

    # Check rate limit
    if not check_and_update_limit(context.user_data, command_name):
        if _is_admin(chat_id):
            logger.info(f"Lifting ratelimit for admin, command {command_name}")
            return True
        logger.info(f"Ratelimiting user {chat_id}, command {command_name}")
        if command_name == "subscribe":
            await message.edit_reply_markup(reply_markup=None)

        await message.reply_text("Sorry, you can only use this command 2 times a day.")
        return False

    return True


def create_request(chat_id, app_data, force_refresh=False):
    """Creates a request dictionary for RabbitMQ"""
    return {
        "chat_id": chat_id,
        "number": app_data["number"],
        "suffix": app_data["suffix"],
        "type": app_data["type"].upper(),
        "year": app_data["year"],
        "force_refresh": force_refresh,
        "last_updated": "0",
    }


async def create_subscription(update, app_data):
    """Handles the user's subscription request"""
    chat = update.effective_chat
    message = update.callback_query.message
    try:
        if await db.add_to_db(
            chat.id,
            app_data["number"],
            app_data["suffix"],
            app_data["type"],
            int(app_data["year"]),
            chat.username,
            chat.first_name,
            chat.last_name,
        ):
            request = create_request(chat.id, app_data)
            await rabbit.publish_message(request)
            await message.reply_text(message_texts["dialog_completion"])
        else:
            await message.reply_text(message_texts["error_subscribe"])
    except Exception as e:
        logger.error(f"Error creating subscription: {e}")
        await message.reply_text(message_texts["error_generic"])


def clean_sub_context(context):
    """Wipes temporary subscription data from user_data context"""
    keys_to_delete = ["application_number", "application_suffix", "application_type", "application_year"]

    for key in keys_to_delete:
        context.user_data.pop(key, None)


async def application_dialog_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = get_effective_message(update)
    logger.info(f"User sends number: {user_info(update)}")
    number = message.text.strip()
    if not number.isdigit() or not (4 <= len(number) <= 5):
        await message.reply_text(message_texts["error_invalid_number"])
        return
    context.user_data["application_number"] = number
    # NOTE(fernflower) Let's hardcode it for now as it's not used in the POST anyway
    context.user_data["application_suffix"] = "0"
    keyboard = [
        [
            InlineKeyboardButton(app_type, callback_data=f"application_dialog_type_{app_type}")
            for app_type in POPULAR_ALLOWED_TYPES
        ],
        [
            InlineKeyboardButton(app_type, callback_data=f"application_dialog_type_{app_type}")
            for app_type in sorted(set(ALLOWED_TYPES) - set(POPULAR_ALLOWED_TYPES))
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(message_texts["dialog_type"], reply_markup=reply_markup)
    return TYPE


async def application_dialog_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if await _is_button_click_abused(update, context):
        return
    await query.answer()

    app_type_callback_str = "application_dialog_type"
    # Process application type
    button_id, app_type = query.data.split(f"{app_type_callback_str}_")
    if app_type in ALLOWED_TYPES:
        # XXX FIXME(fernflower) Later switch to i18n message
        await query.edit_message_text(f"Type {app_type} has been selected")
        context.user_data["application_type"] = app_type
    else:
        logger.warn("Unsupported application type %s", app_type)
        # XXX FIXME(fernflower) Later switch to i18n message
        await query.edit_message_text(f"Unsupported application type {app_type}")
    # Show keyboard for application year selection
    keyboard = [[InlineKeyboardButton(str(year), callback_data=f"application_dialog_year_{year}") for year in ALLOWED_YEARS]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message_texts["dialog_year"], reply_markup=reply_markup)
    return YEAR


async def application_dialog_year(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if await _is_button_click_abused(update, context):
        return
    await query.answer()

    app_year_callback_str = "application_dialog_year"
    try:
        year = int(query.data.split(f"{app_year_callback_str}_")[-1])
    except ValueError:
        logger.error("Something went horribly wrong during year parsing from %s", query.data)
        return
    await query.edit_message_text(f"Year {year} has been selected")
    context.user_data["application_year"] = year
    # Ask user if the entered data is correct
    keyboard = [
        [InlineKeyboardButton(button_texts["subscribe_correct"], callback_data="proceed_subscribe")],
        [InlineKeyboardButton(button_texts["subscribe_incorrect"], callback_data="cancel_subscribe")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    # Choose the appropriate message based on the suffix value
    confirmation_msg = (
        message_texts["dialog_confirmation_no_suffix"]
        if context.user_data["application_suffix"] == "0"
        else message_texts["dialog_confirmation"]
    )
    await query.edit_message_text(
        confirmation_msg.format(
            number=context.user_data["application_number"],
            suffix=context.user_data["application_suffix"],
            type=context.user_data["application_type"],
            year=year,
        ),
        reply_markup=reply_markup,
    )
    return VALIDATE


async def application_dialog_validate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if await _is_button_click_abused(update, context):
        return
    await query.answer()

    if query.data == "proceed_subscribe":
        # check if we are not over daily limit
        if not await enforce_rate_limit(update, context, "subscribe"):
            clean_sub_context(context)
            return

        app_data = {
            "number": context.user_data["application_number"],
            "suffix": context.user_data["application_suffix"],
            "type": context.user_data["application_type"],
            "year": context.user_data["application_year"],
        }

        logger.info(f"Received application details: {app_data} from {user_info(update)}")
        await query.message.edit_reply_markup(reply_markup=None)
        await create_subscription(update, app_data)
        clean_sub_context(context)

    if query.data == "cancel_subscribe":
        await query.message.edit_reply_markup(reply_markup=None)
        await query.message.reply_text(message_texts["dialog_cancel"])
        clean_sub_context(context)
    return ConversationHandler.END


# Handler for the /start command
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows initial message and asks to subscribe"""
    logging.info(f"Received /start command from {user_info(update)}")
    await update.message.reply_text(message_texts["start_text"].format(refresh_period=int(REFRESH_PERIOD / 60)))
    keyboard = [
        [InlineKeyboardButton(button_texts["subscribe_button"], callback_data="subscribe")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(message_texts["subscribe_intro"], reply_markup=reply_markup)
    return START


# Handler for /subscribe command
async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Subscribes user for application status updates.

    If the command has no arguments, then an interactive dialog is started.
    [TBD] If an argument is passed then an attempt to parse it will be made and upon success it will be treated as
    application number.
    """
    logger.info(f"Received /subscribe command from {user_info(update)}")

    message = get_effective_message(update)

    if await db.check_subscription_in_db(message.chat_id):
        await message.reply_text("You are already subscribed")
        return
    else:
        await update.message.reply_text(message_texts["dialog_app_number"])
    return NUMBER


# Callback function for user-pressed-subscribe-button-event
async def subscribe_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if await _is_button_click_abused(update, context):
        return
    await query.answer()

    assert query.data == "subscribe"
    if query.data == "subscribe":
        if await db.check_subscription_in_db(query.message.chat_id):
            await query.edit_message_text("You are already subscribed.")
        else:
            await query.edit_message_text(message_texts["dialog_app_number"])
            return NUMBER


# Handler for /unsubscribe command
async def unsubscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unsubscribes user from application status updates"""
    logger.info(f"Received /unsubscribe command from {user_info(update)}")

    if await db.check_subscription_in_db(update.message.chat_id):
        await db.remove_from_db(update.message.chat_id)
        await update.message.reply_text("You have unsubscribed")
    else:
        await update.message.reply_text("You are not subscribed")


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
            app_data = {
                "number": user_data["application_number"],
                "suffix": user_data["application_suffix"],
                "type": user_data["application_type"].upper(),
                "year": user_data["application_year"],
            }
            request = create_request(message.chat_id, app_data, True)

            logger.info(f"Publishing force refresh for {request}")

            await rabbit.publish_message(request)
            await message.reply_text("Refresh request sent.")
            await message.reply_text(message_texts["cizi_problem_promo"])
        else:
            await message.reply_text("Failed to retrieve user data. Please try again later.")
    except Exception as e:
        logger.error(f"Error creating force refresh request: {e}")
        await message.reply_text(message_texts["error_generic"])


# Handler for /status command
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Returns current status of the application"""
    logger.info(f"Received /status command from {user_info(update)}")

    if await db.check_subscription_in_db(update.message.chat_id):
        app_status = await db.get_application_status_timestamp(update.message.chat_id)
        await update.message.reply_text(app_status)
    else:
        await update.message.reply_text("You are not subscribed")


# Handler for the /help command
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays info on how to use the bot."""
    logger.info(f"Received /help command from {user_info(update)}")
    await update.message.reply_text(message_texts["start_text"].format(refresh_period=int(REFRESH_PERIOD / 60)))


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


# Handler for unknown commands
async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Sorry, I didn't understand that command.")


# TODO make suffix optional
# TODO clean up sub context on new commands?
# TODO handler for /admin_restart_fetcher
# TODO hander for /admin_restart_bot
# TODO handler for /admin_oldest_refresh
