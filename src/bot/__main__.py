from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler, filters
import asyncio
import logging
import signal

from bot.loader import loop, bot, db, rabbit
from bot.handlers import start_command, button, help_command, unknown, status_command, unsubscribe_command, subscribe_command


# set up logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# loop control
running = True


async def shutdown():
    global running
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
    running = False


async def main():
    global running

    # Connect to rabbit
    await rabbit.connect()

    # Install signal handlers for SIGINT and SIGTERM
    signal.signal(signal.SIGINT, lambda s, f: asyncio.create_task(shutdown()))
    signal.signal(signal.SIGTERM, lambda s, f: asyncio.create_task(shutdown()))

    # Register command and message handlers
    bot.add_handler(CommandHandler("start", start_command, has_args=False))
    bot.add_handler(CallbackQueryHandler(button))
    bot.add_handler(CommandHandler("status", status_command, has_args=False))
    bot.add_handler(CommandHandler("subscribe", subscribe_command))
    bot.add_handler(CommandHandler("unsubscribe", unsubscribe_command, has_args=False))
    bot.add_handler(CommandHandler("help", help_command, has_args=False))
    unknown_handler = MessageHandler(filters.COMMAND, unknown)
    bot.add_handler(unknown_handler)

    # Run the bot
    logger.info("Starting telegram bot")
    # https://github.com/python-telegram-bot/python-telegram-bot/wiki/Frequently-requested-design-patterns/#running-ptb-alongside-other-asyncio-frameworks
    await bot.initialize()
    await bot.updater.start_polling()
    await bot.start()

    # Run RabbitMQ consumer
    await rabbit.consume_messages()

    # Main loop
    while running:
        # do some usefull stuff here
        await asyncio.sleep(3)

    logger.info("Main loop has exited")


if __name__ == "__main__":
    loop.run_until_complete(main())
