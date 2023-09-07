from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters
import os
import json
import asyncio
import aio_pika
import logging
import signal
from db import database
from message_queue import rabbitmq


TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890")

subscribe_helper_text = """
Please run command /subscribe with the following arguments
- application number (usually 4 to 5 digits)
- application suffix (put 0, if you don't have it)
- application type (TP,DP,MK, etc...)
- year of application (4 digits)

Example: /subscribe 12345 0 TP 2023

"""

# set up logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
# logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# rabbit and db connectors
rabbit = None
db = None

# loop control
running = True


async def shutdown(app, rabbit, db):
    logger.info("Shutting down...")
    global running
    # Stop bot
    await app.updater.stop()
    await app.stop()
    await app.shutdown()
    # Cleanup
    await rabbit.close()
    await db.close()
    logger.info("Gracefully shut down")
    running = False


# Handler for the /start command
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a message with three inline buttons attached"""
    logging.info(f"Received /start command from {update.message.chat_id}")
    keyboard = [
        [InlineKeyboardButton("Subscribe", callback_data="subscribe")],
        [InlineKeyboardButton("Unsubscribe", callback_data="unsubscribe")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Please choose:", reply_markup=reply_markup)


# Handler for button clicks
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "subscribe":
        if await db.check_subscription_in_db(query.message.chat_id):
            await query.edit_message_text("You are already subscribed.")
        else:
            await query.edit_message_text(subscribe_helper_text)
    elif query.data == "unsubscribe":
        if await db.check_subscription_in_db(query.message.chat_id):
            await db.remove_from_db(query.message.chat_id)
            await query.edit_message_text("You have unsubscribed.")
        else:
            await query.edit_message_text("You are not subscribed.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays info on how to use the bot."""
    await update.message.reply_text("Press bot menu for this list of available commands.")


async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Sorry, I didn't understand that command.")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Returns current status of the application"""
    logger.debug("Received /status command")

    if await db.check_subscription_in_db(update.message.chat_id):
        app_status = await db.get_status_from_db(update.message.chat_id)
        await update.message.reply_text(app_status, parse_mode="HTML")
    else:
        await update.message.reply_text("You are not subscribed")


async def unsubscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unsubscribes user from application status updates"""
    logger.debug("Received /unsubscribe command")

    if await db.check_subscription_in_db(update.message.chat_id):
        await db.remove_from_db(update.message.chat_id)
        await update.message.reply_text("You have unsubscribed")
    else:
        await update.message.reply_text("You are not subscribed")


async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Subscribes user for application status updates"""
    app_data = context.args
    logger.debug(f"Received /subscribe command with args {app_data}")
    if await db.check_subscription_in_db(update.message.chat_id):
        await update.message.reply_text("You are already subscribed")
        return
    try:
        if len(app_data) == 4:
            number, suffix, type, year = context.args
            # Input sanitization
            if not number.isdigit():
                await update.message.reply_text("Invalid application number.")
                return
            if not suffix.isdigit():
                await update.message.reply_text("Invalid suffix. It should be a number.")
                return
            if len(type) != 2:
                await update.message.reply_text("Invalid type. It should be two letters (e.g. TP, DP, MK and so on)")
                return
            if not year.isdigit() or len(year) != 4:
                await update.message.reply_text("Invalid year. It should be 4 digits.")
                return

            message = {
                "chat_id": update.message.chat_id,
                "number": number,
                "suffix": suffix,
                "type": type.upper(),
                "year": year,
            }
            logger.info(f"Received application details {message}")
            # add data to the db
            await db.add_to_db(update.message.chat_id, number, suffix, type.upper(), year)

            # publish request for fetchers
            await rabbit.publish_message(message)

            await update.message.reply_text(
                f"You have been subscribed for application <b>OAM-{number}-{suffix}/{type.upper()}-{year}</b> updates.",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(subscribe_helper_text)
    except Exception as e:
        await update.message.reply_text(f"An error occurred: {e}")


async def main():
    # make rabbit and db connectors global
    global db
    global rabbit
    global running

    # Initialize the database connection
    db = database.Database()

    # Initialize telegram bot
    app = Application.builder().token(TOKEN).build()

    # Initialize rabbit
    rabbit = rabbitmq.RabbitMQ(app, db)
    await rabbit.connect()

    # Install signal handlers for SIGINT and SIGTERM
    signal.signal(signal.SIGINT, lambda s, f: asyncio.create_task(shutdown(app, rabbit, db)))
    signal.signal(signal.SIGTERM, lambda s, f: asyncio.create_task(shutdown(app, rabbit, db)))

    # Register command and message handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("subscribe", subscribe_command))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe_command))
    app.add_handler(CommandHandler("help", help_command))
    unknown_handler = MessageHandler(filters.COMMAND, unknown)
    app.add_handler(unknown_handler)

    # Run the bot until the user presses Ctrl-C
    logger.info("Starting telegram bot")
    # https://github.com/python-telegram-bot/python-telegram-bot/wiki/Frequently-requested-design-patterns/#running-ptb-alongside-other-asyncio-frameworks
    await app.initialize()
    await app.updater.start_polling()
    await app.start()

    # Run RabbitMQ consumer in background
    await rabbit.consume_messages()

    while running:
        # do some useful stuff here
        await asyncio.sleep(3)

    logger.info("Main loop has exited")


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
