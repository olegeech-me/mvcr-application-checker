"""Process-level configuration for the bot service"""

import os

# Version information
BASE_VERSION = os.getenv("BASE_VERSION", "v2.4.4")
GIT_COMMIT = os.getenv("GIT_COMMIT", "unknown")
FULL_VERSION = f"{BASE_VERSION}-{GIT_COMMIT}"

# Telegram bot config
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# PTB v20.5: HTTPS_PROXY / ALL_PROXY also work via httpx when proxy_url is unset
# (see telegram.request.HTTPXRequest). We mirror common env for explicit builder wiring.
PROXY_URL = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or os.getenv("ALL_PROXY")
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

# Notification dispatcher config.
# NOTIFY_RETRY_BASE_INTERVAL doubles as the in-flight delivery lock window
# (next_attempt_at = NOW + this) so a retry's first backoff equals the lock window.
NOTIFY_RETRY_BASE_INTERVAL = int(os.getenv("NOTIFY_RETRY_BASE_INTERVAL", 300))
NOTIFY_RETRY_MAX_INTERVAL = int(os.getenv("NOTIFY_RETRY_MAX_INTERVAL", 3600))
NOTIFY_MONITOR_TICK = int(os.getenv("NOTIFY_MONITOR_TICK", 60))
# Outbox cleanup retention windows (days)
NOTIFY_DELIVERED_RETENTION_DAYS = int(os.getenv("NOTIFY_DELIVERED_RETENTION_DAYS", 1))
NOTIFY_PENDING_MAX_AGE_DAYS = int(os.getenv("NOTIFY_PENDING_MAX_AGE_DAYS", 30))

# DB migrations
DB_MIGRATIONS_DIR = os.getenv("DB_MIGRATIONS_DIR", "db-migrations")

# Prometheus metrics
METRICS_HOST = os.getenv("METRICS_HOST", "0.0.0.0")
METRICS_PORT = int(os.getenv("METRICS_PORT", 8000))

# Run mode for tests
RUN_MODE = os.getenv("RUN_MODE", "PROD")
