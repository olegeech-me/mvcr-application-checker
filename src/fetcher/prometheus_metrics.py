import logging
import time

from prometheus_client import CollectorRegistry, Counter, Gauge, REGISTRY, start_http_server

logger = logging.getLogger(__name__)

# Keep label values bounded so Prometheus never gets user/request cardinality
REQUEST_TYPES = {"fetch", "refresh"}
SOURCES = {"oam", "zov"}
FETCH_RESULTS = {"success", "retry", "failed"}
REQUEST_STATES = {"waiting", "locked"}
ERROR_STAGES = {"browser", "rabbitmq", "latency_check", "status_publish", "processor"}
ERROR_TYPES = {
    "connection",
    "empty_status",
    "publish_failed",
    "status_mismatch",
    "status_too_large",
    "timeout",
    "unexpected",
}


def _safe_label(value, allowed, default="other"):
    if value in allowed:
        return value
    return default


class FetcherPrometheusMetrics:
    """Prometheus metrics exported by fetcher workers"""

    def __init__(self, registry=REGISTRY):
        self.requests_total = Counter(
            "mvcr_fetcher_requests_total",
            "Fetcher request outcomes.",
            ["request_type", "source", "result"],
            registry=registry,
        )
        self.errors_total = Counter(
            "mvcr_fetcher_errors_total",
            "Fetcher operational errors.",
            ["stage", "error_type"],
            registry=registry,
        )
        self.request_state = Gauge(
            "mvcr_fetcher_request_state",
            "Current fetcher request state.",
            ["state"],
            registry=registry,
        )
        self.target_latency_seconds = Gauge(
            "mvcr_fetcher_target_latency_seconds",
            "Latency to the MVCR target website in seconds.",
            registry=registry,
        )
        self.target_up = Gauge(
            "mvcr_fetcher_target_up",
            "Whether the MVCR target website check is healthy.",
            registry=registry,
        )
        self.last_success_timestamp_seconds = Gauge(
            "mvcr_fetcher_last_success_timestamp_seconds",
            "Unix timestamp of the last successful fetch.",
            registry=registry,
        )
        self.build_info = Gauge(
            "mvcr_fetcher_build_info",
            "Fetcher build information.",
            ["version", "commit"],
            registry=registry,
        )

    def record_fetch_result(self, request_type, source, result):
        """Record fetch request result"""
        self.requests_total.labels(
            request_type=_safe_label(request_type, REQUEST_TYPES),
            source=_safe_label(source, SOURCES),
            result=_safe_label(result, FETCH_RESULTS),
        ).inc()

    def record_error(self, stage, error_type):
        """Record fetcher operational error"""
        self.errors_total.labels(
            stage=_safe_label(stage, ERROR_STAGES),
            error_type=_safe_label(error_type, ERROR_TYPES),
        ).inc()

    def set_request_state(self, state, value):
        """Set current number of requests in a state"""
        self.request_state.labels(state=_safe_label(state, REQUEST_STATES)).set(value)

    def set_target_latency(self, seconds):
        """Set latest MVCR target latency"""
        self.target_latency_seconds.set(seconds)

    def set_target_up(self, is_up):
        """Set latest MVCR target health check result"""
        self.target_up.set(1 if is_up else 0)

    def mark_success(self, timestamp=None):
        """Record the latest successful fetch timestamp"""
        self.last_success_timestamp_seconds.set(timestamp or time.time())

    def set_build_info(self, version, commit):
        """Expose fetcher version and git commit"""
        self.build_info.labels(version=version, commit=commit).set(1)


metrics = FetcherPrometheusMetrics()


def start_metrics_server(host, port):
    logger.info("Starting Prometheus metrics server on %s:%s", host, port)
    # prometheus-client exposes the default registry on /metrics
    return start_http_server(port, addr=host)


def set_build_info(version, commit):
    metrics.set_build_info(version, commit)


def record_fetch_result(request_type, source, result):
    metrics.record_fetch_result(request_type, source, result)


def record_error(stage, error_type):
    metrics.record_error(stage, error_type)


def set_request_state(state, value):
    metrics.set_request_state(state, value)


def set_target_latency(seconds):
    metrics.set_target_latency(seconds)


def set_target_up(is_up):
    metrics.set_target_up(is_up)


def mark_success(timestamp=None):
    metrics.mark_success(timestamp)


def new_test_metrics():
    # Tests use a private registry to avoid duplicate collectors
    return FetcherPrometheusMetrics(registry=CollectorRegistry())
