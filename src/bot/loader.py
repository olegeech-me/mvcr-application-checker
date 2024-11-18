import asyncio
import uvloop
import os
from telegram.ext import Application, Defaults
from telegram.constants import ParseMode
from bot import database
from bot import rabbitmq
from bot import metrics

# Version information
BASE_VERSION = os.getenv("BASE_VERSION", "v1.0.0")
GIT_COMMIT = os.getenv("GIT_COMMIT", "unknown")
FULL_VERSION = f"{BASE_VERSION}-{GIT_COMMIT}"
# Telegram bot config
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_IDS = os.getenv("ADMIN_CHAT_IDS", "")
ADMIN_CHAT_IDS = [chat_id.strip() for chat_id in ADMIN_CHAT_IDS.split(",")]
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
# Time in seconds before an application request can be requeued
REQUEUE_THRESHOLD_SECONDS = int(os.getenv("REQUEUE_THRESHOLD_SECONDS", 3600))
# Application monitor config
REFRESH_PERIOD = int(os.getenv("REFRESH_PERIOD", 3600))
SCHEDULER_PERIOD = int(os.getenv("SCHEDULER_PERIOD", 300))
NOT_FOUND_MAX_DAYS = int(os.getenv("NOT_FOUND_MAX_DAYS", 30))
NOT_FOUND_REFRESH_PERIOD = int(os.getenv("NOT_FOUND_REFRESH_PERIOD", 86400))
# Statistics module config
STATISTICS_PERIOD_DAYS = int(os.getenv("STATISTICS_PERIOD_DAYS", 60))
STATISTICS_MIN_TRESHOLD_DAYS = int(os.getenv("STATISTICS_MIN_TRESHOLD_DAYS", 1))
# Run mode for tests
RUN_MODE = os.getenv("RUN_MODE", "PROD")

asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
loop = asyncio.get_event_loop()
defaults = Defaults(parse_mode=ParseMode.HTML)


class Loader:
    def __init__(self):
        self._bot = None
        self._db = None
        self._rabbit = None

    @property
    def bot(self):
        if not self._bot and RUN_MODE != "TEST":
            self._bot = Application.builder().token(TOKEN).defaults(defaults).build()
        return self._bot

    @property
    def db(self):
        if not self._db and RUN_MODE != "TEST":
            self._db = database.Database(
                dbname=DB_NAME,
                user=DB_USER,
                password=DB_PASSWORD,
                host=DB_HOST,
                port=DB_PORT,
                loop=loop,
            )
        return self._db

    @property
    def rabbit(self):
        if not self._rabbit and RUN_MODE != "TEST":
            self._rabbit = rabbitmq.RabbitMQ(
                host=RABBIT_HOST,
                user=RABBIT_USER,
                password=RABBIT_PASSWORD,
                bot=self.bot,
                db=self.db,
                requeue_ttl=REQUEUE_THRESHOLD_SECONDS,
                metrics=metrics.Metrics(),
                loop=loop,
            )
        return self._rabbit


loader = Loader()
