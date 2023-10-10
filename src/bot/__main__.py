from telegram.ext import CallbackQueryHandler, CommandHandler, ConversationHandler, MessageHandler, filters
from telegram.error import NetworkError
import asyncio
import logging
import signal

from bot.loader import loader, loop, LOG_LEVEL, ADMIN_CHAT_IDS
from bot.handlers import start_command, help_command, unknown_text, unknown_command, status_command
from bot.handlers import unsubscribe_command, subscribe_command, admin_stats_command
from bot.handlers import force_refresh_command, subscribe_button, lang_command, set_language_startup, set_language_cmd
from bot.handlers import status_button, unsubscribe_button, force_refresh_button
from bot.handlers import (
    application_dialog_number,
    application_dialog_year,
    application_dialog_type,
    application_dialog_validate,
    START,
    NUMBER,
    TYPE,
    YEAR,
    VALIDATE,
    BROADCAST_TEXT,
    BROADCAST_CONFIRM,
    admin_broadcast_command,
    admin_broadcast_text,
    admin_broadcast_confirm,
    REMINDER_ADD,
    REMINDER_DELETE,
    reminder_command,
    add_reminder,
    reminder_button_callback,
    delete_reminder_callback,
)
from bot import monitor

MAX_RETRIES = 15  # maximum number bot of connection retries
RETRY_DELAY = 5  # delay (in seconds) between retries

# Set up logging
log_level_int = eval(f"logging.{LOG_LEVEL}")
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=log_level_int)
logger = logging.getLogger(__name__)
logger.setLevel(log_level_int)
logging.getLogger("httpx").setLevel(logging.WARNING)

# Get instances of bot, database and rabbitmq (lazy init)
bot = loader.bot
db = loader.db
rabbit = loader.rabbit

# Instantiate application scheduler
app_monitor = monitor.ApplicationMonitor(db=db, rabbit=rabbit)


async def shutdown():
    logger.info("Shutting down scheduler...")
    app_monitor.stop()
    # Stop bot
    logger.info("Shutting down bot...")
    await bot.updater.stop()
    await bot.stop()
    await bot.shutdown()
    # Terminate rabbit & db connections
    logger.info("Shutting down rabbit...")
    await rabbit.close()
    logger.info("Shutting down db...")
    await db.close()
    logger.info("Done.")


async def main():
    # Connect to postgres
    await db.connect()
    # Connect to rabbit
    await rabbit.connect()

    # Install signal handlers for SIGINT and SIGTERM
    signal.signal(signal.SIGINT, lambda s, f: asyncio.create_task(shutdown()))
    signal.signal(signal.SIGTERM, lambda s, f: asyncio.create_task(shutdown()))

    # Register command and message handlers
    bot.add_handler(CommandHandler("status", status_command, has_args=False))
    bot.add_handler(CallbackQueryHandler(status_button, pattern="status_*"))
    bot.add_handler(CommandHandler("unsubscribe", unsubscribe_command, has_args=False))
    bot.add_handler(CallbackQueryHandler(unsubscribe_button, pattern="unsubscribe_*"))
    bot.add_handler(CommandHandler("force_refresh", force_refresh_command, has_args=False))
    bot.add_handler(CallbackQueryHandler(force_refresh_button, pattern="force_refresh_*"))
    bot.add_handler(CommandHandler("admin_stats", admin_stats_command, has_args=False))
    bot.add_handler(CommandHandler("lang", lang_command, has_args=False))
    bot.add_handler(CallbackQueryHandler(set_language_cmd, pattern="set_lang_cmd_*"))
    bot.add_handler(CommandHandler("help", help_command, has_args=False))
    # Define conversatinal handler for user-friendly application dialog
    conv_handler = ConversationHandler(
        allow_reentry=True,
        entry_points=[
            CommandHandler("subscribe", subscribe_command),
            CommandHandler("start", start_command, has_args=False),
        ],
        states={
            START: [
                CallbackQueryHandler(subscribe_button, pattern="subscribe"),
                CallbackQueryHandler(set_language_startup, pattern="set_lang_*"),
            ],
            NUMBER: [MessageHandler(filters.TEXT, application_dialog_number)],
            TYPE: [CallbackQueryHandler(application_dialog_type, pattern="application_dialog_type_*")],
            YEAR: [CallbackQueryHandler(application_dialog_year, pattern="application_dialog_year_*")],
            VALIDATE: [CallbackQueryHandler(application_dialog_validate, pattern="proceed_subscribe|cancel_subscribe")],
        },
        fallbacks=[CommandHandler("subscribe", subscribe_command), CommandHandler("start", start_command, has_args=False)],
    )
    bot.add_handler(conv_handler)
    # Handler to admin broadcast dialog
    broadcast_handler = ConversationHandler(
        entry_points=[CommandHandler("admin_broadcast", admin_broadcast_command)],
        states={
            BROADCAST_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_text)],
            BROADCAST_CONFIRM: [CallbackQueryHandler(admin_broadcast_confirm)],
        },
        fallbacks=[],
    )
    bot.add_handler(broadcast_handler)
    # Handler for /reminder dialog
    reminder_handler = ConversationHandler(
        entry_points=[CommandHandler("reminder", reminder_command)],
        states={
            REMINDER_ADD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_reminder),
                CallbackQueryHandler(reminder_button_callback),
            ],
            REMINDER_DELETE: [
                CallbackQueryHandler(delete_reminder_callback, pattern="^delete_*"),
                CallbackQueryHandler(reminder_button_callback, pattern="^cancel$"),
            ],
        },
        fallbacks=[CommandHandler("reminder", reminder_command)],
    )
    bot.add_handler(reminder_handler)
    bot.add_handler(MessageHandler(filters.TEXT, unknown_text))
    bot.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    # Run the bot
    logger.info("Starting telegram bot")
    for retry in range(1, MAX_RETRIES + 1):
        try:
            await bot.initialize()
            await bot.updater.start_polling()
            await bot.start()
            break
        except NetworkError as e:
            if retry < MAX_RETRIES:
                logger.error(f"Failed to start bot due to network error: {e}")
                await asyncio.sleep(RETRY_DELAY)
            else:
                logger.error("Max retries reached. Could not start telegram bot")
                raise
    logger.info(f"Admins are: {ADMIN_CHAT_IDS}")

    # Run RabbitMQ consumer
    await rabbit.consume_messages()

    # Start ApplicationMonitor
    await asyncio.sleep(15)  # wait some time before running scheduler
    await app_monitor.start()

    logger.info("Main loop has exited")


if __name__ == "__main__":
    loop.run_until_complete(main())
