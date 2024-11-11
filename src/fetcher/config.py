import os

# Version information
BASE_VERSION = os.getenv("BASE_VERSION", "v1.0.0")
GIT_COMMIT = os.getenv("GIT_COMMIT", "unknown")
FULL_VERSION = f"{BASE_VERSION}-{GIT_COMMIT}"
# The URL to fetch
URL = os.getenv("URL", "https://frs.gov.cz/informace-o-stavu-rizeni/")
# The maximum number of retries to connect to RabbitMQ
RETRY_INTERVAL = int(os.getenv("RETRY_INTERVAL", 30))
# Where to save reports for failed fetches
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "output")
# Log verbosity
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
# How long to wait for a page to load
PAGE_LOAD_LIMIT_SECONDS = 20
# How much time wait when captcha is hit
CAPTCHA_WAIT_SECONDS = 120
# The max number of messages a fetcher instance should be consuming at once
MAX_MESSAGES = int(os.getenv("MAX_MESSAGES", 10))
# The max number of message processing attempts
MAX_RETRIES = int(os.getenv("MAX_RETRIES", 10))
# Max time to disperse to refresh requests
JITTER_SECONDS = int(os.getenv("JITTER_SECONDS", 900))
# RabbitMQ settings
RABBIT_HOST = os.getenv("RABBIT_HOST", "localhost")
RABBIT_USER = os.getenv("RABBIT_USER", "bunny_admin")
RABBIT_PASSWORD = os.getenv("RABBIT_PASSWORD", "password")
# RabbitMQ SSL settings
RABBIT_SSL_PORT = int(os.getenv("RABBIT_SSL_PORT", 5671))
RABBIT_SSL_CACERTFILE = os.getenv("RABBIT_SSL_CACERTFILE", "")
RABBIT_SSL_CERTFILE = os.getenv("RABBIT_SSL_CERTFILE", "")
RABBIT_SSL_KEYFILE = os.getenv("RABBIT_SSL_KEYFILE", "")
# Metrics settings
ID = os.getenv("ID", "fetcher")
METRICS_TTL = int(os.getenv("METRICS_TTL", 1800))
METRICS_RATE = int(os.getenv("METRICS_RATE", 600))
METRICS_SEND_INTERVAL = int(os.getenv("METRICS_SEND_INTERVAL", 30))
