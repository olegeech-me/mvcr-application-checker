import os

URL = os.getenv("URL", "https://frs.gov.cz/informace-o-stavu-rizeni/")
RETRY_INTERVAL = int(os.getenv("RETRY_INTERVAL", 30))
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "output")
PAGE_LOAD_LIMIT_SECONDS = 20
CAPTCHA_WAIT_SECONDS = 120
RABBIT_HOST = os.getenv("RABBIT_HOST", "localhost")
RABBIT_USER = os.getenv("RABBIT_USER", "bunny_admin")
RABBIT_PASSWORD = os.getenv("RABBIT_PASSWORD", "password")
