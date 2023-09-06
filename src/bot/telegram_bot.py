from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters
import os
import json
import asyncio
import aio_pika
import psycopg2
import logging
import signal


TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME", "AppTrackerDB")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")
RABBIT_HOST = os.getenv("RABBIT_HOST", "localhost")
RABBIT_USER = os.getenv("RABBIT_USER", "bunny_admin")
RABBIT_PASSWORD = os.getenv("RABBIT_PASSWORD", "password")

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
channel = None
db_conn = None

# loop control
running = True


async def shutdown(app, rabbit, db_conn):
    logger.info("Shutting down...")
    global running
    # Stop bot
    await app.updater.stop()
    await app.stop()
    await app.shutdown()
    # Cleanup
    await rabbit.close()
    db_conn.close()
    logger.info("Gracefully shut down")
    running = False


def init_db():
    conn = psycopg2.connect(dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, host=DB_HOST, port="5432")
    return conn


async def add_to_db(chat_id, application_number, application_suffix, application_type, application_year):
    cur = db_conn.cursor()
    logger.info(f"Adding chatID {chat_id} with application number {application_number} to DB")
    try:
        cur.execute(
            "INSERT INTO Applications (chat_id, application_number, application_suffix, application_type, application_year) VALUES (%s, %s, %s, %s, %s)",
            (chat_id, application_number, application_suffix, application_type, application_year),
        )
        db_conn.commit()
    except Exception as e:
        db_conn.rollback()
        logger.error(f"Error inserting into DB: {e}")
    finally:
        cur.close()


async def update_db_status(chat_id, current_status):
    cur = db_conn.cursor()
    logger.info(f"Updating chatID {chat_id} current status in DB")
    try:
        cur.execute(
            "UPDATE Applications SET current_status = %s, status_changed = True, last_notified = CURRENT_TIMESTAMP WHERE chat_id = %s",
            (current_status, chat_id),
        )
        db_conn.commit()
    except Exception as e:
        db_conn.rollback()
        logger.error(f"Error updating DB: {e}")
    finally:
        cur.close()


async def remove_from_db(chat_id):
    cur = db_conn.cursor()
    logger.info(f"Removing chatID {chat_id} from DB")
    try:
        cur.execute("DELETE FROM Applications WHERE chat_id = %s", (chat_id,))
        db_conn.commit()
    except Exception as e:
        db_conn.rollback()
        logger.error(f"Error deleting from DB: {e}")
    finally:
        cur.close()


async def get_data_from_db(chat_id):
    """Queries the database for application data."""
    try:
        # Assuming conn is your DB connection object. Make sure it's initialized before this function is called.
        with db_conn.cursor() as cur:
            cur.execute("""SELECT current_status, last_updated FROM Applications WHERE chat_id = %s;""", (chat_id,))
            result = cur.fetchone()
            if result is not None:
                current_status, last_updated = result
                return f"Current Status: {current_status}, Last Updated: <b>{last_updated}</b>"
            else:
                return "No data found."
    except Exception as e:
        print(f"Error while fetching data from the database: {e}")
        return "Error while fetching data."


async def connect_to_rabbit():
    connection = await aio_pika.connect_robust(f"amqp://{RABBIT_USER}:{RABBIT_PASSWORD}@{RABBIT_HOST}")
    channel = await connection.channel()
    queue = await channel.declare_queue("StatusUpdateQueue", durable=True)
    return connection, channel, queue


async def on_message(app, message: aio_pika.IncomingMessage):
    """Async function to handle messages from StatusUpdateQueue"""
    async with message.process():
        msg_data = json.loads(message.body.decode("utf-8"))
        logger.info(f"Received status update message: {msg_data}")
        chat_id = msg_data.get("chat_id", None)
        status = msg_data.get("status", None)
        if chat_id and status:
            # Update application status in the DB
            await update_db_status(chat_id, status)

            # Construct the notification text
            notification_text = f"Your application status has been updated: {status}"

            # Notify the user
            try:
                await app.updater.bot.send_message(chat_id=chat_id, text=notification_text, parse_mode="HTML")
                logger.info(f"Sent status update to chatID {chat_id}")
            except Exception as e:
                logger.error(f"Failed to send status update to {chat_id}: {e}")


async def consume_messages(app):
    _connection, _channel, queue = await connect_to_rabbit()

    await queue.consume(lambda message: on_message(app, message))
    logger.info("Started consumer")


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


def check_subscription(chat_id):
    cur = db_conn.cursor()
    try:
        cur.execute("SELECT * FROM Applications WHERE chat_id = %s", (chat_id,))
        return bool(cur.fetchone())
    except Exception as e:
        logger.error(f"Error querying DB: {e}")
        return False
    finally:
        cur.close()


# Handler for button clicks
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "subscribe":
        if check_subscription(query.message.chat_id):
            await query.edit_message_text("You are already subscribed.")
        else:
            await query.edit_message_text(subscribe_helper_text)
    elif query.data == "unsubscribe":
        if check_subscription(query.message.chat_id):
            await remove_from_db(query.message.chat_id)
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

    if check_subscription(update.message.chat_id):
        app_status = await get_data_from_db(update.message.chat_id)
        await update.message.reply_text(app_status, parse_mode="HTML")
    else:
        await update.message.reply_text("You are not subscribed")


async def unsubscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unsubscribes user from application status updates"""
    logger.debug("Received /unsubscribe command")

    if check_subscription(update.message.chat_id):
        await remove_from_db(update.message.chat_id)
        await update.message.reply_text("You have unsubscribed")
    else:
        await update.message.reply_text("You are not subscribed")


async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Subscribes user for application status updates"""
    app_data = context.args
    logger.debug(f"Received /subscribe command with args {app_data}")
    if check_subscription(update.message.chat_id):
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
            await add_to_db(update.message.chat_id, number, suffix, type.upper(), year)

            # publish request for fetchers
            await channel.default_exchange.publish(
                aio_pika.Message(body=json.dumps(message).encode("utf-8")),
                routing_key="ApplicationFetchQueue",
            )
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
    global channel
    global db_conn
    global running

    # Initialize the database connection
    try:
        db_conn = init_db()
    except Exception as e:
        print(f"Failed to connect to the database: {e}")
        return
    logger.info("Connected to the db")

    # Init RabbitMQ
    try:
        rabbit, channel, queue = await connect_to_rabbit()
    except Exception as e:
        print(f"Failed to connect to RabbitMQ: {e}")
        db_conn.close()
        return
    logger.info("Connected to the message queue")

    # Install signal handlers for SIGINT and SIGTERM
    signal.signal(signal.SIGINT, lambda s, f: asyncio.create_task(shutdown(app, rabbit, db_conn)))
    signal.signal(signal.SIGTERM, lambda s, f: asyncio.create_task(shutdown(app, rabbit, db_conn)))

    # Initialize telegram bot
    app = Application.builder().token(TOKEN).build()

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
    asyncio.create_task(consume_messages(app))

    while running:
        # do some useful stuff here
        await asyncio.sleep(3)

    logger.info("Main loop has exited")


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
