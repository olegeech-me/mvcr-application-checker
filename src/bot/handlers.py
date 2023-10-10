import datetime
import logging
import re
import time

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, BotCommandScopeChat, ForceReply
from telegram.ext import ContextTypes, ConversationHandler
from bot.loader import loader, ADMIN_CHAT_IDS, REFRESH_PERIOD
from bot.texts import button_texts, message_texts, commands_description
from bot.utils import generate_oam_full_string

SUBSCRIPTIONS_LIMIT = 5
BUTTON_WAIT_SECONDS = 1
FORCE_FETCH_LIMIT_SECONDS = 86400
COMMANDS_LIST = ["status", "subscribe", "unsubscribe", "force_refresh", "lang", "start", "help", "reminder"]
ADMIN_COMMANDS = ["admin_stats", "admin_broadcast"]
DEFAULT_LANGUAGE = "EN"
LANGUAGE_LIST = ["EN 🏴󠁧󠁢󠁥󠁮󠁧󠁿|🇺🇸", "RU 🇷🇺", "CZ 🇨🇿", "UA 🇺🇦"]
IETF_LANGUAGE_MAP = {"en": "EN", "ru": "RU", "cs": "CZ", "uk": "UA"}
ALLOWED_TYPES = ["CD", "DO", "DP", "DV", "MK", "PP", "ST", "TP", "VP", "ZK", "ZM"]
POPULAR_ALLOWED_TYPES = ["DP", "TP", "ZM", "ST", "MK", "DV"]
ALLOWED_YEARS = [y for y in range(datetime.datetime.today().year - 3, datetime.datetime.today().year + 1)]


START, NUMBER, TYPE, YEAR, VALIDATE = range(5)
BROADCAST_TEXT, BROADCAST_CONFIRM = range(2)
REMINDER_ADD, REMINDER_DELETE = range(2)


logger = logging.getLogger(__name__)

# Get instances of database and rabbitmq (lazy init)
db = loader.db
rabbit = loader.rabbit


async def _set_menu_commands(update: Update, context: ContextTypes.DEFAULT_TYPE, lang="EN"):
    """Sets available bot menu commands"""
    logger.debug(f"Setting menu commands for {user_info(update)}, language: {lang}")
    commands = [BotCommand(cmd, commands_description[lang][cmd]) for cmd in COMMANDS_LIST]
    if _is_admin(update.effective_chat.id):
        logger.debug(f"Adding admin menu commands for chat_id {update.effective_chat.id}, ADMIN_CHAT_IDS={ADMIN_CHAT_IDS}")
        commands.extend([BotCommand(cmd, commands_description[lang][cmd]) for cmd in ADMIN_COMMANDS])
    await context.bot.set_my_commands(commands, BotCommandScopeChat(update.effective_chat.id))


async def _get_user_language(update, context):
    """Fetch user language preference"""
    user_lang = context.user_data.get("lang")

    if not user_lang:
        user_lang = await db.fetch_user_language(update.effective_chat.id)
        if not user_lang:
            # Get the language from user locale and try to match
            # it against supported languages
            user_lang = IETF_LANGUAGE_MAP.get(update.effective_user.language_code) or DEFAULT_LANGUAGE
        context.user_data["lang"] = user_lang

    return user_lang


def _is_admin(chat_id: str) -> bool:
    """Check if the user's chat_id is an admin's chat_id"""
    logger.debug(f"Effective chat_id: '{chat_id}', Allowed admin ids: '{ADMIN_CHAT_IDS}'")
    return str(chat_id) in ADMIN_CHAT_IDS


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

    if len(valid_timestamps) >= 5:
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
        "suffix": app_data.get("suffix", "0"),
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
        if await db.insert_application(
            chat.id,
            app_data["number"],
            app_data.get("suffix", "0"),
            app_data["type"],
            int(app_data["year"]),
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
        # check if the subscription already exists
        if await db.subscription_exists(
            update.effective_chat.id,
            context.user_data["application_number"],
            context.user_data["application_type"],
            int(context.user_data["application_year"]),
        ):
            await query.message.edit_reply_markup(reply_markup=None)
            await query.message.reply_text(message_texts[lang]["already_subscribed"])
            clean_sub_context(context)
            return ConversationHandler.END

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

        logger.info(
            f"[SUBSCRIBE] Adding application OAM-"
            f"{app_data['number']}-{app_data['suffix']}/{app_data['type']}-{app_data['year']} for {user_info(update)}"
        )
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
    await _set_menu_commands(update, context, lang)

    # Prompt the user to select a language if it's the default (assumed they haven't set it yet)
    start_msg = message_texts[lang]["start_text"].format(refresh_period=int(REFRESH_PERIOD / 60))
    subscribe_msg = message_texts[lang]["subscribe_intro"]
    msg = f"{start_msg}\n\n{subscribe_msg}"
    keyboard = [[InlineKeyboardButton(button_texts[lang]["subscribe_button"], callback_data="subscribe")]]
    if show_language_switch:
        keyboard.append([InlineKeyboardButton(lang, callback_data=f"set_lang_{lang}") for lang in LANGUAGE_LIST])
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, reply_markup=reply_markup)
    else:
        await update.message.reply_text(msg, reply_markup=reply_markup)


async def _create_user_in_db_if_not_exists(update, lang="EN"):
    """Create user in the database if it does not exist"""
    chat_id = update.effective_chat.id
    if not await db.user_exists(chat_id):
        await db.insert_user(
            chat_id,
            update.effective_chat.first_name,
            update.effective_chat.username,
            update.effective_chat.last_name,
            lang=lang,
        )


# Handler for the /start command
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows initial message and asks to subscribe"""
    lang = await _get_user_language(update, context)
    await _create_user_in_db_if_not_exists(update, lang=lang)

    logging.info(f"💻 Received /start command from {user_info(update)}")
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
    logger.info(f"💻 Received /subscribe command from {user_info(update)}")

    lang = await _get_user_language(update, context)
    message = get_effective_message(update)
    chat_id = message.chat_id

    await _create_user_in_db_if_not_exists(update, lang=lang)

    # Verify how many subscriptions user has (not more than SUBSCRIPTIONS_LIMIT)
    if await db.count_user_subscriptions(chat_id) >= SUBSCRIPTIONS_LIMIT:
        await message.reply_text(message_texts[lang]["max_subscriptions_reached"])
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
        if await db.count_user_subscriptions(query.message.chat_id) >= SUBSCRIPTIONS_LIMIT:
            await query.edit_message_text(message_texts[lang]["max_subscriptions_reached"])
        else:
            await query.edit_message_text(message_texts[lang]["dialog_app_number"])
            return NUMBER


def _generate_buttons_from_subscriptions(prefix, subscriptions):
    """Generate inline keyboard from the user's subscriptions"""

    keyboard = []

    for sub in subscriptions:
        # Construct button label
        suffix_part = f"-{sub['application_suffix']}" if sub["application_suffix"] != "0" else ""
        button_label = f"OAM-{sub['application_number']}{suffix_part}/{sub['application_type']}-{sub['application_year']}"

        # Construct button callback data
        callback_data = f"{prefix}_{sub['application_number']}-{sub['application_type']}-{sub['application_year']}"

        # Append to the keyboard list
        keyboard.append([InlineKeyboardButton(button_label, callback_data=callback_data)])

    return InlineKeyboardMarkup(keyboard)


def _parse_application_buttons_callback_data(data):
    """Parses application buttons callback data and returns app_details dict"""
    data_part = data.split("_")[-1]
    application_number, application_type, application_year = data_part.split("-")
    return {
        "number": application_number,
        "type": application_type,
        "year": int(application_year),
    }


# Handler for /unsubscribe command
async def unsubscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unsubscribes user from application status updates"""
    logger.info(f"💻 Received /unsubscribe command from {user_info(update)}")
    lang = await _get_user_language(update, context)
    chat_id = update.message.chat_id

    # Get all subscriptions and ask user which one he wants to
    # unsubscribe from
    subscriptions = await db.fetch_user_subscriptions(chat_id)
    if len(subscriptions) > 1:
        keyboard = _generate_buttons_from_subscriptions("unsubscribe", subscriptions)
        await update.message.reply_text(message_texts[lang]["select_unsubscribe"], reply_markup=keyboard)
    # If user has a single subscription - remove it right away
    elif len(subscriptions) == 1:
        if await db.delete_application(
            chat_id,
            subscriptions[0]["application_number"],
            subscriptions[0]["application_type"],
            subscriptions[0]["application_year"],
        ):
            oam_full_string = generate_oam_full_string(subscriptions[0])
            await update.message.reply_text(message_texts[lang]["unsubscribe"].format(app_string=oam_full_string))
        else:
            await update.message.reply_text(message_texts[lang]["unsubscribe_failed"])
    else:
        await update.message.reply_text(message_texts[lang]["not_subscribed"])


async def unsubscribe_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Removes selected user subscriptions"""
    query = update.callback_query
    chat_id = update.effective_chat.id
    lang = await _get_user_language(update, context)

    if await _is_button_click_abused(update, context):
        return
    await query.answer()

    app_details = _parse_application_buttons_callback_data(query.data)
    await db.delete_application(
        chat_id,
        app_details["number"],
        app_details["type"],
        app_details["year"],
    )
    oam_full_string = generate_oam_full_string(app_details)
    await query.edit_message_text(message_texts[lang]["unsubscribe"].format(app_string=oam_full_string))


async def _publish_force_request(update, caller, lang, app_details):
    """Publishes a force refresh request"""
    if caller == "button":
        target_message = update.callback_query
        reply_function = target_message.message.reply_text
    else:
        target_message = get_effective_message(update)
        reply_function = target_message.reply_text

    try:
        request = create_request(update.effective_chat.id, app_details, True)
        oam_full_string = generate_oam_full_string(request)
        logger.info(f"Publishing force refresh for {oam_full_string}, user: {request['chat_id']}")
        await rabbit.publish_message(request)

        if caller == "button":
            await target_message.edit_message_text(message_texts[lang]["refresh_sent"])
        else:
            await reply_function(message_texts[lang]["refresh_sent"])

        await reply_function(message_texts[lang]["cizi_problem_promo"])

    except Exception as e:
        logger.error(f"Error creating force refresh request: {e}")
        await reply_function(message_texts[lang]["failed_to_refresh"])


# Handler for /force_refresh command
async def force_refresh_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force refresh application status"""
    logger.info(f"💻 Received /force_refresh command from {user_info(update)}")
    message = get_effective_message(update)
    lang = await _get_user_language(update, context)
    chat_id = update.effective_chat.id

    subscriptions = await db.fetch_user_subscriptions(chat_id)
    if not subscriptions:
        await message.reply_text(message_texts[lang]["not_subscribed"])
        return
    if not await enforce_rate_limit(update, context, "force_refresh", lang=lang):
        return
    if len(subscriptions) > 1:
        keyboard = _generate_buttons_from_subscriptions("force_refresh", subscriptions)
        await update.message.reply_text(message_texts[lang]["select_refresh"], reply_markup=keyboard)
    else:
        await _publish_force_request(
            update,
            "cli",
            lang,
            {
                "number": subscriptions[0]["application_number"],
                "type": subscriptions[0]["application_type"],
                "year": subscriptions[0]["application_year"],
            },
        )


async def force_refresh_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends force refresh request for selected application"""
    query = update.callback_query
    lang = await _get_user_language(update, context)

    if await _is_button_click_abused(update, context):
        return
    await query.answer()

    app_details = _parse_application_buttons_callback_data(query.data)
    await _publish_force_request(update, "button", lang, app_details)


# Handler for /status command
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Returns current status of the application"""
    logger.info(f"💻 Received /status command from {user_info(update)}")
    message = update.message
    chat_id = update.effective_chat.id
    lang = await _get_user_language(update, context)

    subscriptions = await db.fetch_user_subscriptions(chat_id)
    if len(subscriptions) > 1:
        keyboard = _generate_buttons_from_subscriptions("status", subscriptions)
        await message.reply_text(message_texts[lang]["select_status"], reply_markup=keyboard)
    elif len(subscriptions) == 1:
        app_status = await db.fetch_status_with_timestamp(
            chat_id,
            subscriptions[0]["application_number"],
            subscriptions[0]["application_type"],
            subscriptions[0]["application_year"],
            lang=lang,
        )
        await message.reply_text(app_status)
    else:
        await message.reply_text(message_texts[lang]["not_subscribed"])


async def status_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Retrieves current status for the selected application"""

    query = update.callback_query
    chat_id = update.effective_chat.id
    lang = await _get_user_language(update, context)

    if await _is_button_click_abused(update, context):
        return
    await query.answer()

    app_details = _parse_application_buttons_callback_data(query.data)
    app_status = await db.fetch_status_with_timestamp(
        chat_id,
        app_details["number"],
        app_details["type"],
        app_details["year"],
        lang=lang,
    )
    await query.edit_message_text(app_status)


# Handler for the /help command
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays info on how to use the bot."""
    logger.info(f"💻 Received /help command from {user_info(update)}")
    lang = await _get_user_language(update, context)
    await update.message.reply_text(message_texts[lang]["start_text"].format(refresh_period=int(REFRESH_PERIOD / 60)))


# handler for /admin_stats
async def admin_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return admin statistics"""
    logging.info(f"💻 Received /admin_stats command from {user_info(update)}")
    if not _is_admin(update.effective_chat.id):
        await update.message.reply_text("Unauthorized. This command is only for admins.")
        return

    user_count = await db.count_users_total()
    subscribed_users = await db.count_subscribed_users()
    active_users = await db.count_active_users()
    if user_count is not None:
        await update.message.reply_text(
            f"👥 Total users: <b>{user_count}</b>\n"
            f"✉️ Subscribed users: <b>{subscribed_users}</b>\n"
            f"🔍 Users with active (non-resolved) subscriptions: <b>{active_users}</b>\n"
        )
    else:
        await update.message.reply_text("Error retrieving statistics.")


# Handler for /admin_broadcast
async def admin_broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Initiates the admin broadcasting process"""
    logger.info(f"💻 Received /admin_broadcast command from {user_info(update)}")

    # Check authorization
    if not _is_admin(update.effective_chat.id):
        await update.message.reply_text("Unauthorized. This command is only for admins.")
        return ConversationHandler.END

    # Prompt the admin for the broadcast message
    await update.message.reply_text("Please provide the message to be broadcasted.", reply_markup=ForceReply(selective=True))
    return BROADCAST_TEXT


async def admin_broadcast_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the provided broadcast message and asks for confirmation"""
    user_message = update.message.text

    # Store the broadcast message in the context for future reference
    context.user_data["broadcast_message"] = user_message

    total_users = await db.count_users_total()
    confirmation_text = (
        f"This will be broadcasted to {total_users} users. Message:\n\n{user_message}\n\n" "Do you confirm to send this?"
    )
    keyboard = [
        [InlineKeyboardButton("Yes", callback_data="confirm_broadcast")],
        [InlineKeyboardButton("No", callback_data="cancel_broadcast")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(confirmation_text, reply_markup=reply_markup)
    return BROADCAST_CONFIRM


async def admin_broadcast_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the confirmation of the broadcast and sends the message to all users"""
    query = update.callback_query
    user_message = context.user_data.get("broadcast_message")

    if query.data == "confirm_broadcast":
        chat_ids = await db.fetch_all_chat_ids()

        for chat_id in chat_ids:
            try:
                await context.bot.send_message(chat_id, user_message)
            except Exception as e:
                logger.error(f"Error broadcasting to chat_id {chat_id}: {e}")

        logger.warning(f"📢 Admin broadcast message was issued to all users by {user_info(update)}")
        await query.edit_message_text("Message broadcasted successfully!")
    elif query.data == "cancel_broadcast":
        await query.edit_message_text("Broadcast canceled.")

    # Clear the stored broadcast message from the context
    context.user_data.pop("broadcast_message", None)

    return ConversationHandler.END


# Handler for /reminder
async def reminder_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /reminder command."""
    logger.info(f"💻 Received /reminder command from {user_info(update)}")
    chat_id = update.effective_chat.id
    lang = await _get_user_language(update, context)

    # Fetch existing reminders from the DB
    reminders = await db.fetch_user_reminders(chat_id)

    # If user already has reminders
    if reminders:
        formatted_reminders = []

        # Create a list of all the reminders in a readable format
        for reminder in reminders:
            oam_full_string = generate_oam_full_string(reminder)
            reminder_string = "- " + str(reminder["reminder_time"])[:-3] + f" {oam_full_string}"
            formatted_reminders.append(reminder_string)

        # Join the reminders into a single string, separated by newlines
        reminders_str = "\n".join(formatted_reminders)

        # Prompt to either add a new reminder or delete an existing one
        row = [
            InlineKeyboardButton(button_texts[lang]["add_reminder"], callback_data="add_reminder"),
            InlineKeyboardButton(button_texts[lang]["delete_reminder"], callback_data="delete_reminder"),
        ]
        keyboard = [row, [InlineKeyboardButton(button_texts[lang]["cancel"], callback_data="cancel")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            message_texts[lang]["reminder_decision"].format(reminders_str=reminders_str), reply_markup=reply_markup
        )
        return REMINDER_ADD
    else:
        # Fetch user applications (subscriptions) from the DB
        applications = await db.fetch_user_subscriptions(chat_id)

        # If no applications/subscriptions, notify the user
        if not applications:
            await update.message.reply_text(message_texts[lang]["not_subscribed"])
            return ConversationHandler.END

        # Display list of user applications for selection
        keyboard = []
        for app in applications:
            app_details = generate_oam_full_string(app)
            button = InlineKeyboardButton(app_details, callback_data=f"selectapp_{app['application_id']}")
            keyboard.append([button])

        keyboard.append([InlineKeyboardButton(button_texts[lang]["cancel"], callback_data="cancel")])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(message_texts[lang]["select_application_for_reminder"], reply_markup=reply_markup)
        return REMINDER_ADD


async def reminder_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reminder button callback"""
    query = update.callback_query
    lang = await _get_user_language(update, context)

    if await _is_button_click_abused(update, context):
        return
    await query.answer()

    # If user chose to delete reminders
    if query.data == "delete_reminder":
        # Fetch user reminders
        reminders = await db.fetch_user_reminders(query.message.chat_id)

        row = []
        for reminder in reminders:
            callback_data = f"delete_{reminder['reminder_id']}"
            button_label = str(reminder["reminder_time"])[:-3]
            row.append(InlineKeyboardButton(button_label, callback_data=callback_data))

        keyboard = [row, [InlineKeyboardButton(button_texts[lang]["cancel"], callback_data="cancel")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(message_texts[lang]["select_reminder_to_delete"], reply_markup=reply_markup)
        return REMINDER_DELETE

    # If user chose to add a new reminder
    elif query.data == "add_reminder":
        reminders = await db.fetch_user_reminders(query.message.chat_id)
        # Check if user already has 2 reminders
        if len(reminders) >= 2:
            await query.edit_message_text(message_texts[lang]["max_reminders_reached"])
            return ConversationHandler.END
        else:
            await query.edit_message_text(message_texts[lang]["enter_reminder_time"])
            return REMINDER_ADD

    # User chose appcalition to create reminder for
    elif query.data.startswith("selectapp_"):
        _, app_id = query.data.split("_")
        # Save application ID in context for use in the next step
        context.user_data["selected_app_id"] = int(app_id)
        await query.edit_message_text(message_texts[lang]["enter_reminder_time"])
        return REMINDER_ADD

    # User doesn't know what to do
    elif query.data == "cancel":
        await query.edit_message_text(message_texts[lang]["action_canceled"])
        return ConversationHandler.END


async def delete_reminder_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete reminder callback"""
    query = update.callback_query
    lang = await _get_user_language(update, context)
    chat_id = update.effective_chat.id

    if await _is_button_click_abused(update, context):
        return
    await query.answer()

    if query.data == "cancel":
        await query.edit_message_text(message_texts[lang]["action_canceled"])
    else:
        # Extract the reminder_id from the callback data
        _, reminder_id = query.data.split("_")

        # Delete from DB
        success = await db.delete_reminder(chat_id, int(reminder_id))

        if success:
            await query.edit_message_text(message_texts[lang]["reminder_deleted"])
        else:
            await query.edit_message_text(message_texts[lang]["reminder_delete_failed"])

    return ConversationHandler.END


def validate_time_format(time_str: str) -> bool:
    """Validates time format HH:MM"""
    return bool(re.match(r"^([01]\d|2[0-3]):?([0-5]\d)$", time_str))


async def add_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Adds reminder to the database"""
    lang = await _get_user_language(update, context)
    time_input = update.message.text.strip()

    if not validate_time_format(time_input):
        await update.message.reply_text(message_texts[lang]["invalid_time_format"])
        return REMINDER_ADD

    chat_id = update.effective_chat.id

    # Fetch existing reminders from the DB
    reminders = await db.fetch_user_reminders(chat_id)
    existing_times = [str(reminder["reminder_time"])[:-3] for reminder in reminders]

    # Check if time_input already exists for this user
    if time_input in existing_times:
        await update.message.reply_text(message_texts[lang]["reminder_time_exists"])
        return REMINDER_ADD

    # Get the stored application ID from context
    application_id = context.user_data.get("selected_app_id", None)
    if not application_id:
        await update.message.reply_text(message_texts[lang]["application_not_selected"])
        return ConversationHandler.END

    success = await db.insert_reminder(chat_id, time_input, application_id)

    if success:
        logger.info(f"Reminder added for {time_input}, application_id: {application_id}, user {user_info(update)}")
        await update.message.reply_text(message_texts[lang]["reminder_added"])
    else:
        logger.error(f"Failed to add reminder {time_input}, application_id: {application_id}, user {user_info(update)}")
        await update.message.reply_text(message_texts[lang]["reminder_add_failed"])

    return ConversationHandler.END


# Handler for /lang command
async def lang_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /lang command to set language preference"""
    logging.info(f"💻 Received /lang command from {user_info(update)}")

    row = [InlineKeyboardButton(lang, callback_data=f"set_lang_cmd_{lang}") for lang in LANGUAGE_LIST]
    keyboard = [row]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text("Choose language / Выберете язык / Zvolte jazyk / Виберіть мову:", reply_markup=reply_markup)
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

    # If user exists in DB, update preference in DB
    if await db.user_exists(chat_id):
        await db.update_user_language(chat_id, selected_lang)

    await query.edit_message_text(message_texts[selected_lang]["language_selected"].format(lang=selected_lang_with_emoji))
    return selected_lang


async def set_language_startup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback function for language selection during start up"""

    await _set_language(update, context, "set_lang_")
    # Show subscribe message again
    await _show_startup_message(update, context)
    return START


async def set_language_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback function for language selection during /lang command"""
    lang = await _set_language(update, context, "set_lang_cmd_")
    await _set_menu_commands(update, context, lang)


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
