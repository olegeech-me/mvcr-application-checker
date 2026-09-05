import logging
import time

from prometheus_client import (
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    start_http_server,
)

logger = logging.getLogger(__name__)

# Keep label values bounded so Prometheus never gets user/request cardinality
ERROR_STAGES = {"telegram", "rabbitmq", "db", "notification_dispatcher", "scheduler"}
ERROR_TYPES = {
    "connection",
    "db_error",
    "missing_fetcher_id",
    "network",
    "permanent_send_error",
    "publish_failed",
    "timeout",
    "unexpected",
}
NOTIFICATION_KINDS = {"status_change", "failed_fetch", "force_refresh_unchanged", "expiration"}
NOTIFICATION_RESULTS = {"ok", "dead_user", "retryable_gave_up", "permanent_other"}
QUEUES = {"StatusUpdateQueue", "ExpirationQueue", "FetcherMetricsQueue", "ApplicationFetchQueue", "RefreshStatusQueue"}
MESSAGE_RESULTS = {"processed", "failed", "dropped", "ignored"}
PUBLISH_RESULTS = {"published", "duplicate_skipped", "failed"}
SCHEDULERS = {"application_monitor", "reminder_monitor", "notification_dispatcher"}
SCHEDULER_RESULTS = {"success", "failed"}


def _safe_label(value, allowed, default="other"):
    if value in allowed:
        return value
    return default


class BotPrometheusMetrics:
    """Prometheus metrics exported by the bot service"""

    def __init__(self, registry=REGISTRY):
        self.errors_total = Counter(
            "mvcr_bot_errors_total",
            "Bot operational errors.",
            ["stage", "error_type"],
            registry=registry,
        )
        self.notifications_total = Counter(
            "mvcr_bot_notifications_total",
            "Notification delivery outcomes.",
            ["notification_kind", "result"],
            registry=registry,
        )
        self.rabbitmq_messages_total = Counter(
            "mvcr_bot_rabbitmq_messages_total",
            "Bot RabbitMQ message handling outcomes.",
            ["queue", "result"],
            registry=registry,
        )
        self.published_messages_total = Counter(
            "mvcr_bot_published_messages_total",
            "Messages published by the bot to RabbitMQ.",
            ["queue", "result"],
            registry=registry,
        )
        self.scheduler_runs_total = Counter(
            "mvcr_bot_scheduler_runs_total",
            "Bot scheduler loop run outcomes.",
            ["scheduler", "result"],
            registry=registry,
        )
        self.build_info = Gauge(
            "mvcr_bot_build_info",
            "Bot build information.",
            ["version", "commit"],
            registry=registry,
        )
        self.telegram_last_ok_timestamp_seconds = Gauge(
            "mvcr_bot_telegram_last_ok_timestamp_seconds",
            "Unix timestamp of the last successful Telegram send or received update.",
            registry=registry,
        )

    def record_error(self, stage, error_type):
        """Record a bot operational error"""
        self.errors_total.labels(
            stage=_safe_label(stage, ERROR_STAGES),
            error_type=_safe_label(error_type, ERROR_TYPES),
        ).inc()

    def record_notification(self, notification_kind, result):
        """Record notification delivery result"""
        self.notifications_total.labels(
            notification_kind=_safe_label(notification_kind, NOTIFICATION_KINDS),
            result=_safe_label(result, NOTIFICATION_RESULTS),
        ).inc()

    def record_rabbitmq_message(self, queue, result):
        """Record consumed RabbitMQ message result"""
        self.rabbitmq_messages_total.labels(
            queue=_safe_label(queue, QUEUES),
            result=_safe_label(result, MESSAGE_RESULTS),
        ).inc()

    def record_published_message(self, queue, result):
        """Record published RabbitMQ message result"""
        self.published_messages_total.labels(
            queue=_safe_label(queue, QUEUES),
            result=_safe_label(result, PUBLISH_RESULTS),
        ).inc()

    def record_scheduler_run(self, scheduler, result):
        """Record scheduler loop result"""
        self.scheduler_runs_total.labels(
            scheduler=_safe_label(scheduler, SCHEDULERS),
            result=_safe_label(result, SCHEDULER_RESULTS),
        ).inc()

    def set_build_info(self, version, commit):
        """Expose bot version and git commit"""
        self.build_info.labels(version=version, commit=commit).set(1)

    def set_telegram_last_ok(self):
        """Stamp the last time a Telegram send or inbound update succeeded"""
        self.telegram_last_ok_timestamp_seconds.set(time.time())


metrics = BotPrometheusMetrics()


def start_metrics_server(host, port):
    logger.info("Starting Prometheus metrics server on %s:%s", host, port)
    # prometheus-client exposes the default registry on /metrics
    return start_http_server(port, addr=host)


def set_build_info(version, commit):
    metrics.set_build_info(version, commit)


def record_error(stage, error_type):
    metrics.record_error(stage, error_type)


def record_notification(notification_kind, result):
    metrics.record_notification(notification_kind, result)


def record_rabbitmq_message(queue, result):
    metrics.record_rabbitmq_message(queue, result)


def record_published_message(queue, result):
    metrics.record_published_message(queue, result)


def record_scheduler_run(scheduler, result):
    metrics.record_scheduler_run(scheduler, result)


def set_telegram_last_ok():
    metrics.set_telegram_last_ok()


def new_test_metrics():
    # Tests use a private registry to avoid duplicate collectors
    return BotPrometheusMetrics(registry=CollectorRegistry())
