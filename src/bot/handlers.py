import datetime
import logging
import re
import time

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, ConversationHandler
from bot.loader import loader, ADMIN_CHAT_ID, REFRESH_PERIOD
from bot.texts import button_texts, message_texts


BUTTON_WAIT_SECONDS = 1
FORCE_FETCH_LIMIT_SECONDS = 86400
DEFAULT_LANGUAGE = "EN"
LANGUAGE_LIST = ["EN 🏴󠁧󠁢󠁥󠁮󠁧󠁿|🇺🇸", "RU 🇷🇺", "CZ 🇨🇿", "UA 🇺🇦"]
IETF_LANGUAGE_MAP = {"en": "EN", "ru": "RU", "cs": "CZ", "uk": "UA"}
ALLOWED_TYPES = ["CD", "DO", "DP", "DV", "MK", "PP", "ST", "TP", "VP", "ZK", "ZM"]
POPULAR_ALLOWED_TYPES = ["DP", "TP", "ZM", "ST", "MK", "DV"]
ALLOWED_YEARS = [y for y in range(datetime.datetime.today().year - 3, datetime.datetime.today().year + 1)]


START, NUMBER, TYPE, YEAR, VALIDATE = range(5)


logger = logging.getLogger(__name__)

# Get instances of database and rabbitmq (lazy init)
db = loader.db
rabbit = loader.rabbit


async def _get_user_language(update, context):
    """Fetch user language preference"""
    user_lang = context.user_data.get("lang")

    if not user_lang:
        user_lang = await db.get_user_language(update.effective_chat.id)

        if not user_lang:
            # Get the language from user locale and try to match
            # it against supported languages
            user_lang = IETF_LANGUAGE_MAP.get(update.effective_user.language_code) or DEFAULT_LANGUAGE

        context.user_data["lang"] = user_lang

    return user_lang


def _is_admin(chat_id: str) -> bool:
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


async def enforce_rate_limit(update: Update, context: ContextTypes.DEFAULT_TYPE, command_name: str, lang="EN"):
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

        await message.reply_text(message_texts[lang]["ratelimit_exceeded"])
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
        "failed": False,
        "request_type": "fetch",
        "last_updated": "0",
    }


async def create_subscription(update, app_data, lang="EN"):
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
            lang,
        ):
            request = create_request(chat.id, app_data)
            await rabbit.publish_message(request)
            await message.reply_text(message_texts[lang]["dialog_completion"])
        else:
            await message.reply_text(message_texts[lang]["error_subscribe"])
    except Exception as e:
        logger.error(f"Error creating subscription: {e}")
        await message.reply_text(message_texts[lang]["error_generic"])


def clean_sub_context(context):
    """Wipes temporary subscription data from user_data context"""
    keys_to_delete = ["application_number", "application_suffix", "application_type", "application_year"]

    for key in keys_to_delete:
        context.user_data.pop(key, None)


async def _show_app_number_final_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Ask user if the entered data is correct
    lang = await _get_user_language(update, context)
    keyboard = [
        [InlineKeyboardButton(button_texts[lang]["subscribe_correct"], callback_data="proceed_subscribe")],
        [InlineKeyboardButton(button_texts[lang]["subscribe_incorrect"], callback_data="cancel_subscribe")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    # Choose the appropriate message based on the suffix value
    confirmation_msg = (
        message_texts[lang]["dialog_confirmation_no_suffix"]
        if context.user_data["application_suffix"] == "0"
        else message_texts[lang]["dialog_confirmation"]
    )
    msg = confirmation_msg.format(
        number=context.user_data["application_number"],
        suffix=context.user_data["application_suffix"],
        type=context.user_data["application_type"],
        year=context.user_data["application_year"],
    )
    # Note(fernflower) Let's unify update for message and callback_queries: if callback_query is not set then it's
    # a message update
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, reply_markup=reply_markup)
    else:
        message = get_effective_message(update)
        await message.reply_text(msg, reply_markup=reply_markup)
    return VALIDATE


def _parse_application_number_full(num_str: str):
    """
    Parses supplied application number OAM-13077/ZK-2020.
    Application number may or may not contain OAM prefix or integer suffix.
    FULL_REGEX = (OAM-){0,1}[0-9]{3,5}(-[0-9]+){0,1}/[A-Z]{2}-[0-9]{4}
    This function returns a tuple (number, suffix, type, year) in case of success or None otherwise.
    """
    num_str = num_str.replace(" ", "").upper()
    num_regex = r"^(OAM-){0,1}([0-9]{3,5})(-[0-9]+){0,1}/([A-Z]{2})-([0-9]{4})$"
    matched = re.match(num_regex, num_str)
    if not matched:
        return
    # check that type and number are among allowed
    if matched[4] not in ALLOWED_TYPES or int(matched[5]) not in ALLOWED_YEARS:
        logger.info("Application type %s or year %s are unsupported", matched[4], matched[5])
        return
    return matched[2], (matched[3] or "0").lstrip("-"), matched[4], matched[5]


def _parse_application_number(num_str: str):
    """
    Parses number part of supplied application number OAM-13077/ZK-2020.
    Application number may or may not contain OAM prefix or integer suffix.
    FULL_REGEX = (OAM-){0,1}[0-9]{3,5}(-[0-9]+){0,1}/[A-Z]{2}-[0-9]{4}
    This function returns a tuple (number, suffix) in case of success or None otherwise.
    """
    num_str = num_str.replace(" ", "").upper()
    num_regex = r"^(OAM-){0,1}([0-9]{3,5})(-[0-9]+){0,1}$"
    matched = re.match(num_regex, num_str)
    if not matched:
        return
    return matched[2], (matched[3] or "0").lstrip("-")


async def application_dialog_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Gets full application number to work with.
    Two ways of operation are supported:
    * User submits just the number part of the application (useful for mobile device). In this case an interactive
      dialog is triggered and the ConversationHandler will walk the user through the steps of specifying type and year
    * User submits full application number in 1111/TP-2022 form (useful for copy-paste from official documents). If
      it is parsed successfully there is no need for the interactive dialog, so it's skipped.
    """
    message = get_effective_message(update)
    lang = await _get_user_language(update, context)
    number_str = message.text.strip()
    # NOTE(fernflower) Attempt to parse full application number as is
    number_parsed = _parse_application_number_full(number_str)
    if number_parsed:
        context.user_data["application_number"] = number_parsed[0]
        context.user_data["application_suffix"] = number_parsed[1]
        context.user_data["application_type"] = number_parsed[2]
        context.user_data["application_year"] = number_parsed[3]
        # go straight to verification step
        await _show_app_number_final_confirmation(update, context)
        return VALIDATE
    # NOTE(fernflower) Okay, full match failed, let's try partial match just for number part (no type and year) and
    # get the rest via interactive dialog
    number_parsed = _parse_application_number(message.text.strip())
    if not number_parsed:
        await message.reply_text(message_texts[lang]["error_invalid_number"])
        return
    context.user_data["application_number"] = number_parsed[0]
    context.user_data["application_suffix"] = number_parsed[1]
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
    await update.message.reply_text(message_texts[lang]["dialog_type"], reply_markup=reply_markup)
    return TYPE


async def application_dialog_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = await _get_user_language(update, context)
    if await _is_button_click_abused(update, context):
        return
    await query.answer()

    app_type_callback_str = "application_dialog_type"
    # Process application type
    button_id, app_type = query.data.split(f"{app_type_callback_str}_")
    if app_type in ALLOWED_TYPES:
        # XXX FIXME(fernflower) Later switch to i18n message
        context.user_data["application_type"] = app_type
    else:
        logger.warn("Unsupported application type %s", app_type)
        # XXX FIXME(fernflower) Later switch to i18n message
        await query.edit_message_text(f"Unsupported application type {app_type}")
    # Show keyboard for application year selection
    keyboard = [[InlineKeyboardButton(str(year), callback_data=f"application_dialog_year_{year}") for year in ALLOWED_YEARS]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message_texts[lang]["dialog_year"], reply_markup=reply_markup)
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
    context.user_data["application_year"] = year
    await _show_app_number_final_confirmation(update, context)
    return VALIDATE


async def application_dialog_validate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = await _get_user_language(update, context)
    if await _is_button_click_abused(update, context):
        return
    await query.answer()

    if query.data == "proceed_subscribe":
        # check if we are not over daily limit
        if not await enforce_rate_limit(update, context, "subscribe", lang=lang):
            clean_sub_context(context)
            return

        app_data = {
            "number": context.user_data["application_number"],
            "suffix": context.user_data["application_suffix"],
            "type": context.user_data["application_type"],
            "year": context.user_data["application_year"],
        }

        logger.info(f"[SUBSCRIBE] Received application details: {app_data} from {user_info(update)}")
        await query.message.edit_reply_markup(reply_markup=None)
        await create_subscription(update, app_data, lang=lang)
        clean_sub_context(context)

    if query.data == "cancel_subscribe":
        await query.message.edit_reply_markup(reply_markup=None)
        await query.message.reply_text(message_texts[lang]["dialog_cancel"])
        clean_sub_context(context)
    return ConversationHandler.END


async def _show_startup_message(update: Update, context: ContextTypes.DEFAULT_TYPE, show_language_switch=True):
    """
    Shows initial message.
    If show_language_switch if True then language select menu will be shown as well.
    """
    lang = await _get_user_language(update, context)
    # Prompt the user to select a language if it's the default (assumed they haven't set it yet)
    start_msg = message_texts[lang]["start_text"].format(refresh_period=int(REFRESH_PERIOD / 60))
    subscribe_msg = message_texts[lang]["subscribe_intro"]
    msg = f"{start_msg}\n{subscribe_msg}"
    keyboard = [[InlineKeyboardButton(button_texts[lang]["subscribe_button"], callback_data="subscribe")]]
    if show_language_switch:
        keyboard.append([InlineKeyboardButton(lang, callback_data=f"set_lang_{lang}") for lang in LANGUAGE_LIST])
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, reply_markup=reply_markup)
    else:
        await update.message.reply_text(msg, reply_markup=reply_markup)


# Handler for the /start command
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows initial message and asks to subscribe"""
    logging.info(f"Received /start command from {user_info(update)}")
    await _show_startup_message(update, context)
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

    lang = await _get_user_language(update, context)
    message = get_effective_message(update)

    if await db.check_subscription_in_db(message.chat_id):
        await message.reply_text(message_texts[lang]["already_subscribed"])
        return
    else:
        # Let's first check if the user has supplied any arguments. If he did - let's treat them as application number
        # and if the format matches - skip the interactive dialog.
        if context.args:
            number_str = "".join(context.args)
            number_parsed = _parse_application_number_full(number_str)
            if number_parsed:
                context.user_data["application_number"] = number_parsed[0]
                context.user_data["application_suffix"] = number_parsed[1]
                context.user_data["application_type"] = number_parsed[2]
                context.user_data["application_year"] = number_parsed[3]
                # go straight to verification step
                await _show_app_number_final_confirmation(update, context)
                return VALIDATE
        await update.message.reply_text(message_texts[lang]["dialog_app_number"])

    return NUMBER


# Callback function for user-pressed-subscribe-button-event
async def subscribe_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = await _get_user_language(update, context)

    if await _is_button_click_abused(update, context):
        return
    await query.answer()

    if query.data == "subscribe":
        if await db.check_subscription_in_db(query.message.chat_id):
            await query.edit_message_text(message_texts[lang]["already_subscribed"])
        else:
            await query.edit_message_text(message_texts[lang]["dialog_app_number"])
            return NUMBER


# Handler for /unsubscribe command
async def unsubscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unsubscribes user from application status updates"""
    logger.info(f"Received /unsubscribe command from {user_info(update)}")
    lang = await _get_user_language(update, context)

    if await db.check_subscription_in_db(update.message.chat_id):
        await db.remove_from_db(update.message.chat_id)
        await update.message.reply_text(message_texts[lang]["unsubscribe"])
    else:
        await update.message.reply_text(message_texts[lang]["not_subscribed"])


# Handler for /force_refresh command
async def force_refresh_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force refresh application status."""
    logger.info(f"Received /force_refresh command from {user_info(update)}")
    message = get_effective_message(update)
    lang = await _get_user_language(update, context)

    if not await db.check_subscription_in_db(message.chat_id):
        await message.reply_text(message_texts[lang]["not_subscribed"])
        return
    if not await enforce_rate_limit(update, context, "force_refresh", lang=lang):
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
            await message.reply_text(message_texts[lang]["refresh_sent"])
            await message.reply_text(message_texts[lang]["cizi_problem_promo"])
        else:
            await message.reply_text(message_texts[lang]["failed_to_refresh"])
    except Exception as e:
        logger.error(f"Error creating force refresh request: {e}")
        await message.reply_text(message_texts[lang]["error_generic"])


# Handler for /status command
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Returns current status of the application"""
    logger.info(f"Received /status command from {user_info(update)}")
    lang = await _get_user_language(update, context)

    if await db.check_subscription_in_db(update.message.chat_id):
        app_status = await db.get_application_status_timestamp(update.message.chat_id, lang=lang)
        await update.message.reply_text(app_status)
    else:
        await update.message.reply_text(message_texts[lang]["not_subscribed"])


# Handler for the /help command
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays info on how to use the bot."""
    logger.info(f"Received /help command from {user_info(update)}")
    lang = await _get_user_language(update, context)
    await update.message.reply_text(message_texts[lang]["start_text"].format(refresh_period=int(REFRESH_PERIOD / 60)))


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


# Handler for /lang command
async def lang_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /lang command to set language preference"""
    logging.info(f"Received /lang command from {user_info(update)}")

    keyboard = [[InlineKeyboardButton(lang, callback_data=f"set_lang_cmd_{lang}")] for lang in LANGUAGE_LIST]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Select your language / Выберете язык / Zvolte jazyk:", reply_markup=reply_markup)
    return START


async def _set_language(update: Update, context: ContextTypes.DEFAULT_TYPE, cmd_string: str):
    """Set up user language"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    selected_lang_with_emoji = query.data.split(f"{cmd_string}")[1]

    # Extract the clean key without emoji
    selected_lang = selected_lang_with_emoji.split()[0]

    # Store language in user_data for the session
    context.user_data["lang"] = selected_lang
    logger.info(f"Language set to {selected_lang} for {user_info(update)}")

    # If user has subscription, update preference in DB
    if await db.check_subscription_in_db(chat_id):
        await db.set_user_language(chat_id, selected_lang)

    await query.edit_message_text(message_texts[selected_lang]["language_selected"].format(lang=selected_lang_with_emoji))


async def set_language_startup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback function for language selection during start up"""

    await _set_language(update, context, "set_lang_")
    # Show subscribe message again
    await _show_startup_message(update, context)
    return START


async def set_language_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback function for language selection during /lang command"""
    await _set_language(update, context, "set_lang_cmd_")


async def _get_incorrect_message_text(context: ContextTypes.DEFAULT_TYPE, lang="EN"):
    """Counts how many times user put unkwown input and returns appropriate message"""
    current_incorrect_count = context.user_data.get("incorrect_input_count", 0)
    current_incorrect_count += 1
    if current_incorrect_count >= 3:
        msg = message_texts[lang]["unknown_input_funny"]
        context.user_data["incorrect_input_count"] = 0
    else:
        msg = message_texts[lang]["unknown_input"]
        context.user_data["incorrect_input_count"] = current_incorrect_count

    return msg


# Handler for unknown text inputs when out of subscribe context
async def unknown_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await _get_user_language(update, context)
    msg = await _get_incorrect_message_text(context, lang=lang)

    await context.bot.send_message(chat_id=update.effective_chat.id, text=msg)


# Handler for unknown commands
async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = await _get_user_language(update, context)
    msg = await _get_incorrect_message_text(context, lang=lang)

    await context.bot.send_message(chat_id=update.effective_chat.id, text=msg)
