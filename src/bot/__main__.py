import asyncio
import logging
import signal

from telegram import Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    TypeHandler,
    filters,
)

from bot import monitor, prometheus_metrics
from bot.config import (
    ADMIN_CHAT_IDS,
    BASE_VERSION,
    DB_MIGRATIONS_DIR,
    FULL_VERSION,
    GIT_COMMIT,
    LOG_LEVEL,
    METRICS_HOST,
    METRICS_PORT,
)
from bot.handlers import (
    BROADCAST_CONFIRM,
    BROADCAST_TEXT,
    NUMBER,
    REMINDER_ADD,
    REMINDER_DELETE,
    SOURCE,
    START,
    TYPE,
    VALIDATE,
    YEAR,
    add_reminder,
    admin_broadcast_command,
    admin_broadcast_confirm,
    admin_broadcast_text,
    admin_stats_command,
    application_dialog_number,
    application_dialog_source,
    application_dialog_type,
    application_dialog_validate,
    application_dialog_year,
    delete_reminder_callback,
    fetcher_stats_command,
    force_refresh_button,
    force_refresh_command,
    help_command,
    lang_command,
    reminder_button_callback,
    reminder_command,
    set_language_cmd,
    set_language_startup,
    start_command,
    status_button,
    status_command,
    subscribe_button,
    subscribe_command,
    unknown_command,
    unknown_text,
    unsubscribe_button,
    unsubscribe_command,
)
from bot.loader import loader, loop

MAX_RETRIES = 15  # maximum number bot of connection retries
RETRY_DELAY = 5  # delay (in seconds) between retries


async def record_telegram_inbound_activity(update, context):
    """Inbound updates prove polling is alive"""
    prometheus_metrics.set_telegram_last_ok()


async def telegram_error_handler(update, context):
    """Meter runtime Telegram failures from polling and handler delivery"""
    exc = context.error
    if isinstance(exc, TimedOut):
        prometheus_metrics.record_error("telegram", "timeout")
        logger.warning("Telegram TimedOut while handling an update")
    elif isinstance(exc, NetworkError):
        prometheus_metrics.record_error("telegram", "network")
        logger.warning(f"Telegram NetworkError while handling an update: {exc}")


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

# Instantiate reminder scheduler
reminder_monitor = monitor.ReminderMonitor(db=db, rabbit=rabbit)

# Notification dispatcher (drains the Notifications outbox).
# Resolved via loader so RabbitMQ holds the same instance and can wake() it
notification_dispatcher = loader.notification_dispatcher


async def shutdown():
    logger.info("Shutting down schedulers...")
    app_monitor.stop()
    reminder_monitor.stop()
    notification_dispatcher.stop()
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
    prometheus_metrics.start_metrics_server(METRICS_HOST, METRICS_PORT)
    prometheus_metrics.set_build_info(BASE_VERSION, GIT_COMMIT)
    prometheus_metrics.set_telegram_last_ok()
    # Connect to postgres
    await db.connect(migrations_dir=DB_MIGRATIONS_DIR)
    # Connect to rabbit
    await rabbit.connect()

    # Install signal handlers for SIGINT and SIGTERM
    signal.signal(signal.SIGINT, lambda s, f: asyncio.create_task(shutdown()))
    signal.signal(signal.SIGTERM, lambda s, f: asyncio.create_task(shutdown()))

    # group -1 so this runs before command handlers (which block group 0)
    bot.add_handler(
        TypeHandler(Update, record_telegram_inbound_activity, block=False),
        group=-1,
    )

    # Register command and message handlers
    bot.add_handler(CommandHandler("status", status_command, has_args=False))
    bot.add_handler(CallbackQueryHandler(status_button, pattern="status_*"))
    bot.add_handler(CommandHandler("unsubscribe", unsubscribe_command, has_args=False))
    bot.add_handler(CallbackQueryHandler(unsubscribe_button, pattern="unsubscribe_*"))
    bot.add_handler(CommandHandler("force_refresh", force_refresh_command, has_args=False))
    bot.add_handler(CallbackQueryHandler(force_refresh_button, pattern="force_refresh_*"))
    bot.add_handler(CommandHandler("admin_stats", admin_stats_command, has_args=False))
    bot.add_handler(CommandHandler("fetcher_stats", fetcher_stats_command, has_args=False))
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
            SOURCE: [CallbackQueryHandler(application_dialog_source, pattern="application_source_*")],
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
    bot.add_error_handler(telegram_error_handler)

    # Run the bot
    logger.info("Starting telegram bot")
    for retry in range(1, MAX_RETRIES + 1):
        try:
            await bot.initialize()
            await bot.updater.start_polling()
            await bot.start()
            break
        except NetworkError as e:
            prometheus_metrics.record_error("telegram", "network")
            if retry < MAX_RETRIES:
                logger.error(f"Failed to start bot due to network error: {e}")
                await asyncio.sleep(RETRY_DELAY)
            else:
                logger.error("Max retries reached. Could not start telegram bot")
                raise
    prometheus_metrics.set_telegram_last_ok()
    logger.info(f"Admins are: {ADMIN_CHAT_IDS}")

    # Run RabbitMQ consumers
    asyncio.gather(
        rabbit.consume_update_messages(),
        rabbit.consume_expiration_messages(),
        rabbit.consume_service_messages(),
    )

    # Start ApplicationMonitor, ReminderMonitor and NotificationDispatcher
    await asyncio.sleep(15)  # wait some time before running schedulers
    await asyncio.gather(
        app_monitor.start(),
        reminder_monitor.start(),
        notification_dispatcher.start(),
    )

    logger.info("Main loop has exited")


if __name__ == "__main__":
    logger.info(f"Starting Bot version {FULL_VERSION}")
    loop.run_until_complete(main())
