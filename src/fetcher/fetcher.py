"""
Collect application status
"""
import time
import logging
import os
import random
import json
import urllib3
import pika
from pyvirtualdisplay import Display
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

import utils


URL = os.getenv("URL", "https://frs.gov.cz/informace-o-stavu-rizeni/")
# interval to wait before repeating the request
POLLING_INTERVAL = int(os.getenv("POLLING_INTERVAL", "30"))
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "output")
PAGE_LOAD_LIMIT_SECONDS = 20
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "localhost")

# globals to reuse for browser page displaying
DISPLAY = None
BROWSER = None

# set up logging
logging.basicConfig()
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


def _close_browser():
    global BROWSER
    global DISPLAY
    if BROWSER:
        BROWSER.quit()
        BROWSER = None
    if DISPLAY:
        DISPLAY.stop()
        DISPLAY = None


def _get_browser(force=False):
    global DISPLAY
    global BROWSER
    if not force and BROWSER:
        return BROWSER
    DISPLAY = Display(visible=0, size=(1420, 1080))
    DISPLAY.start()
    logger.info("Initialized virtual display")
    options = webdriver.firefox.options.Options()
    options.set_preference("intl.accept_languages", "cs-CZ")
    options.set_preference("http.response.timeout", PAGE_LOAD_LIMIT_SECONDS)
    # set user-agent
    useragent = utils.get_useragent()
    logger.info("User-Agent for this request will be %s", useragent)
    options.set_preference("general.useragent.override", useragent)
    options.set_preference("dom.webdriver.enabled", False)
    options.set_preference("useAutomationExtension", False)
    options.headless = False
    BROWSER = webdriver.Firefox(options=options)
    BROWSER.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    # emulate some user actions tbd
    # BROWSER.maximize_window()
    return BROWSER


def submit_form(browser, app_details):
    """Submit application details into the form"""
    logger.debug(f"Submitting application data for {app_details}")

    WebDriverWait(browser, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".input__control")))
    # Locate and fill out the application number field by its placeholder
    application_number_field = browser.find_element(By.XPATH, "//input[@placeholder='12345']")
    application_number_field.clear()
    application_number_field.send_keys(app_details["number"])

    # Locate and fill out the application type field by its placeholder
    application_suffix_field = browser.find_element(By.XPATH, "//input[@placeholder='XX']")
    application_suffix_field.clear()
    application_suffix_field.send_keys(app_details["suffix"])

    # Trigger type dropdown menu to appear
    menu1 = browser.find_element_by_xpath(
        "//div[contains(@class, 'react-select') and ancestor::div[contains(@style, 'width: 140px;')]]"
    )
    menu1.find_element_by_xpath("//div[contains(@class, 'react-select__control')]").click()

    # Locate and select the type dropdown by placeholder
    scroll1 = browser.find_element_by_xpath("//div[contains(@class, 'react-select__menu')]")
    scroll1_option = scroll1.find_element_by_xpath(f".//div[text()='{app_details['type']}']")
    browser.execute_script("arguments[0].click();", scroll1_option)

    # Trigger year dropdown menu to appear
    menu2 = browser.find_element_by_xpath(
        "//div[contains(@class, 'react-select') and ancestor::div[contains(@style, 'width: 100px;')]]"
    )
    menu2.find_element_by_xpath(".//div[contains(@class, 'react-select__control')]").click()

    # Locate and select the year dropdown by placeholder
    scroll2 = menu2.find_element_by_xpath(".//div[contains(@class, 'react-select__menu')]")
    scroll2_option = scroll2.find_element_by_xpath(f".//div[text()='{app_details['year']}']")
    browser.execute_script("arguments[0].click();", scroll2_option)

    # Locate the submit button and click it to submit the form
    submit_button = browser.find_element(By.CSS_SELECTOR, 'button[type="submit"]')
    browser.execute_script("arguments[0].click();", submit_button)


def _do_fetch_with_browser(url, app_details, wait_for_javascript=PAGE_LOAD_LIMIT_SECONDS, wait_for_class="wrapper__form"):
    def _has_recaptcha(browser):
        # captcha = browser.find_elements(
        #    By.CSS_SELECTOR, "iframe[name^='a-'][src^='https://www.google.com/recaptcha/api2/anchor?']"
        # )
        # return bool(captcha)
        # olegeech: never caught captcha for now, so this is just a stub for now
        return False

    def _save_page_source(browser):
        # save page source in case of issues
        out_file = f"{OUTPUT_DIR}/{app_details['number']}-{app_details['type']}-{app_details['year']}.html"
        page_source = browser.page_source
        if page_source:
            with open(out_file, "w") as f:
                f.write(page_source)

    browser = _get_browser()
    application_status_text = None

    try:
        browser.get(url)
        WebDriverWait(browser, wait_for_javascript).until(
            lambda x: _has_recaptcha(x) or x.find_element(By.CLASS_NAME, wait_for_class)
        )

        if _has_recaptcha(browser):
            logger.warning("Recaptcha has been hit, solve it please to continue")
            WebDriverWait(browser, 120).until(lambda x: x.find_element(By.CLASS_NAME, wait_for_class))

        submit_form(browser, app_details)

        WebDriverWait(browser, wait_for_javascript).until(
            lambda x: _has_recaptcha(x) or x.find_element(By.CLASS_NAME, "alert__content")
        )

        application_status = browser.find_element_by_class_name("alert__content")
        application_status_text = application_status.get_attribute("innerHTML")

    except (WebDriverException, urllib3.exceptions.MaxRetryError) as err:
        logger.error("An error has occurred during page loading %s", err)
        _save_page_source(browser)
        _close_browser()

    return application_status_text


def fetch(url, app_details, retry_interval=POLLING_INTERVAL, fetch_func=_do_fetch_with_browser, attempts=3):
    """
    Fetches application status. If request fails for some reason will retry N times.
    """
    logger.debug(f"Starting fetcher for {app_details}")
    res = fetch_func(url=url, app_details=app_details)
    attempts_left = attempts
    while attempts_left and not res:
        attempts_left -= 1
        retry_in = int(retry_interval / 3 + random.randint(1, int(2 * retry_interval / 3)))
        print(f"Looks like connection error, will try {url} again later in {retry_in}")
        time.sleep(retry_in)
        res = fetch_func(url=url)
    return res


def main():
    """Connect to the message queue, run fetch for the application data, post back status"""

    # app_details = json.loads('{"number": "5777", "suffix": "3", "type": "TP", "year": "2023"}')
    # app_status = fetch(URL, app_details)
    # logger.info(f"Status is :{app_status}")
    # return
    connection = pika.BlockingConnection(
        pika.ConnectionParameters(host=RABBITMQ_HOST, credentials=pika.PlainCredentials("bunny_admin", "password"))
    )
    channel = connection.channel()

    channel.queue_declare(queue="ApplicationFetchQueue", durable=True)
    channel.queue_declare(queue="StatusUpdateQueue", durable=True)

    def callback(ch, method, properties, body):
        app_details = json.loads(body.decode("utf-8"))
        logger.info("Received application details %s", app_details)

        app_status = fetch(URL, app_details)
        if app_status:
            logger.info(f"Successfully fetch status for application number {app_details['number']}")
            channel.basic_publish(
                exchange="",
                routing_key="StatusUpdateQueue",
                body=json.dumps({"chat_id": app_details["chat_id"], "status": app_status}),
            )
            logger.debug("Message was pushed to StateUpdateQueue")
        else:
            logger.error(f"Failed to fetch status for application number {app_details['number']}")
            # do some magic to restore the message??

    # This will block and wait for messages from ApplicationFetchQueue
    channel.basic_consume(queue="ApplicationFetchQueue", on_message_callback=callback, auto_ack=True)
    channel.start_consuming()


if __name__ == "__main__":
    main()
