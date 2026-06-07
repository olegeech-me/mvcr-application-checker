"""Lazy-initialised singletons: PTB Application, Database, RabbitMQ, NotificationDispatcher"""

import asyncio
import uvloop

from telegram.ext import Application, Defaults
from telegram.constants import ParseMode

from bot import database
from bot import fetcher_stats
from bot import monitor
from bot import rabbitmq
from bot.config import (
    TOKEN,
    PROXY_URL,
    RUN_MODE,
    DB_NAME,
    DB_USER,
    DB_PASSWORD,
    DB_HOST,
    DB_PORT,
    RABBIT_HOST,
    RABBIT_USER,
    RABBIT_PASSWORD,
    REQUEUE_THRESHOLD_SECONDS,
)

asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
loop = asyncio.get_event_loop()
defaults = Defaults(parse_mode=ParseMode.HTML)


class Loader:
    def __init__(self):
        self._bot = None
        self._db = None
        self._rabbit = None
        self._notification_dispatcher = None

    @property
    def bot(self):
        if not self._bot and RUN_MODE != "TEST":
            builder = Application.builder().token(TOKEN).defaults(defaults)
            if PROXY_URL:
                # v20.x: ApplicationBuilder.proxy_url / get_updates_proxy_url
                # (renamed to .proxy / .get_updates_proxy in later PTB releases)
                builder = builder.proxy_url(PROXY_URL).get_updates_proxy_url(PROXY_URL)
            self._bot = builder.build()
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
    def notification_dispatcher(self):
        if not self._notification_dispatcher and RUN_MODE != "TEST":
            self._notification_dispatcher = monitor.NotificationDispatcher(
                db=self.db,
                bot=self.bot,
            )
        return self._notification_dispatcher

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
                fetcher_stats=fetcher_stats.FetcherStats(),
                loop=loop,
                notification_dispatcher=self.notification_dispatcher,
            )
        return self._rabbit


loader = Loader()
