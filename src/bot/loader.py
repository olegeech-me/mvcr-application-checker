import asyncio
import uvloop
import os
from telegram.ext import Application, Defaults
from telegram.constants import ParseMode
from bot import database
from bot import rabbitmq

# Telegram bot config
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
# DB config
DB_NAME = os.getenv("DB_NAME", "AppTrackerDB")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", 5432)
# Rabbit config
RABBIT_HOST = os.getenv("RABBIT_HOST", "localhost")
RABBIT_USER = os.getenv("RABBIT_USER", "bunny_admin")
RABBIT_PASSWORD = os.getenv("RABBIT_PASSWORD", "password")
# Application monitor config
REFRESH_PERIOD = int(os.getenv("REFRESH_PERIOD", 3600))
SCHEDULER_PERIOD = int(os.getenv("SCHEDULER_PERIOD", 300))

asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
loop = asyncio.get_event_loop()
defaults = Defaults(parse_mode=ParseMode.HTML)

# globals to store bot, db and rabbit instances which will be populated after a call to init function
BOT = None
DB = None
RABBIT = None


# Init bot, db and rabbit
def init_bot():
    global BOT
    BOT = BOT or Application.builder().token(TOKEN).defaults(defaults).build()
    return BOT


def init_db():
    global DB
    DB = DB or database.Database(
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT,
        loop=loop,
    )
    return DB


def init_rabbit(bot_instance, db_instance):
    global RABBIT
    RABBIT = RABBIT or rabbitmq.RabbitMQ(
        host=RABBIT_HOST,
        user=RABBIT_USER,
        password=RABBIT_PASSWORD,
        bot=bot_instance,
        db=db_instance,
        loop=loop,
    )
    return RABBIT


def init():
    bot_instance = init_bot()
    db_instance = init_db()
    rabbit_instance = init_rabbit(bot_instance, db_instance)
    return bot_instance, db_instance, rabbit_instance
