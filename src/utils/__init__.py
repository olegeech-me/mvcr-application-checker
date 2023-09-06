import datetime
import os
import requests

import fake_useragent

DATETIME_FORMAT = "%d/%m/%Y %H:%M:%S"
# UA = fake_useragent.UserAgent(browsers=["firefox"], os="windows", min_percentage=50.3)
UA = fake_useragent.UserAgent(browsers=["firefox"])


def get_useragent(ua=UA):
    useragent = ua.random
    # useragent = ua.ff

    return useragent


async def do_fetch(url, logger, proxy=None):
    try:
        proxies = {} if proxy in ("0", "None", "no", None) else {"https": f"socks5h://{proxy}"}
        if proxies:
            logger.info("Using proxy %s for request", proxy)
        resp = requests.get(
            url, proxies=proxies, headers={"Cache-Control": "no-cache", "Pragma": "no-cache", "User-agent": get_useragent()}
        )
    except (requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError):
        return
    except Exception as exc:
        logger.error("Some unexpected exception has occured %s..", exc)
        return
    if resp.ok:
        return resp.text


def timestamp_to_str(timestamp, dt_format=DATETIME_FORMAT):
    """Convert timestamp to a human-readable format"""
    try:
        int_timestamp = int(float(timestamp))
        return datetime.datetime.fromtimestamp(int_timestamp).strftime(dt_format)
    except (ValueError, TypeError):
        return ""


def get_modification_time(filename, human_readable=False):
    modified_ts = os.path.getmtime(filename)
    if not human_readable:
        return modified_ts
    return timestamp_to_str(modified_ts)
