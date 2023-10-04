"""
Use selenium browser to interact with website

Ideas borrowed from https://github.com/fernflower/trvalypobytexamchecker/blob/main/src/fetcher/a2exams_fetcher.py
"""

import logging
import asyncio
import random
import os
from pyvirtualdisplay import Display
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import WebDriverException
from urllib3.exceptions import MaxRetryError
import fake_useragent

from fetcher.config import PAGE_LOAD_LIMIT_SECONDS, CAPTCHA_WAIT_SECONDS, OUTPUT_DIR, RETRY_INTERVAL

logger = logging.getLogger(__name__)


class Browser:
    def __init__(self, retries=3):
        self.display = None
        self.browser = None
        self.retries = retries
        self.app_details = {}

    def _log(self, log_level, message, *args):
        """Wrapper around logger to add application number to the log messages."""
        msg = f"[{self.app_details['number']}] {message}"
        logger.log(log_level, msg, *args)

    def _get_useragent(self):
        ua = fake_useragent.UserAgent(browsers=["firefox"])
        return ua.random

    def _init_browser(self):
        # set user-agent
        useragent = self._get_useragent()
        self._log(logging.INFO, "User-Agent for this request will be %s", useragent)
        # configure display & options
        self.display = Display(visible=0, size=(1420, 1080))
        self.display.start()
        self._log(logging.INFO, "Initialized virtual display")
        options = webdriver.firefox.options.Options()
        options.set_preference("intl.accept_languages", "cs-CZ")
        options.set_preference("http.response.timeout", PAGE_LOAD_LIMIT_SECONDS)
        options.set_preference("general.useragent.override", useragent)
        options.set_preference("dom.webdriver.enabled", False)
        options.set_preference("useAutomationExtension", False)
        options.headless = False
        self.browser = webdriver.Firefox(options=options)
        self.browser.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        # emulate some user actions tbd
        # self.BROWSER.maximize_window()

    def _get_browser(self, force=False):
        if not force and self.browser:
            return self.browser
        self._init_browser()
        return self.browser

    # All the other browser related functions, for example: submit_form

    def _submit_form(self, app_details):
        """Submit application details into the form"""
        logged_details = {key: app_details[key] for key in ["number", "suffix", "type", "year"]}
        self._log(logging.INFO, "Submitting application data %s", logged_details)

        WebDriverWait(self.browser, PAGE_LOAD_LIMIT_SECONDS).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".input__control"))
        )
        # Locate and fill out the application number field by its placeholder
        application_number_field = self.browser.find_element(By.XPATH, "//input[@placeholder='12345']")
        application_number_field.clear()
        application_number_field.send_keys(app_details["number"])

        # Locate and fill out the application type field by its placeholder
        application_suffix_field = self.browser.find_element(By.XPATH, "//input[@placeholder='XX']")
        application_suffix_field.clear()
        application_suffix_field.send_keys(app_details["suffix"])

        # Trigger type dropdown menu to appear
        menu1 = self.browser.find_element_by_xpath(
            "//div[contains(@class, 'react-select') and ancestor::div[contains(@style, 'width: 140px;')]]"
        )
        menu1.find_element_by_xpath("//div[contains(@class, 'react-select__control')]").click()

        # Locate and select the type dropdown by placeholder
        scroll1 = self.browser.find_element_by_xpath("//div[contains(@class, 'react-select__menu')]")
        scroll1_option = scroll1.find_element_by_xpath(f".//div[text()='{app_details['type']}']")
        self.browser.execute_script("arguments[0].click();", scroll1_option)

        # Trigger year dropdown menu to appear
        menu2 = self.browser.find_element_by_xpath(
            "//div[contains(@class, 'react-select') and ancestor::div[contains(@style, 'width: 100px;')]]"
        )
        menu2.find_element_by_xpath(".//div[contains(@class, 'react-select__control')]").click()

        # Locate and select the year dropdown by placeholder
        scroll2 = menu2.find_element_by_xpath(".//div[contains(@class, 'react-select__menu')]")
        scroll2_option = scroll2.find_element_by_xpath(f".//div[text()='{app_details['year']}']")
        self.browser.execute_script("arguments[0].click();", scroll2_option)

        # Locate the submit button and click it to submit the form
        submit_button = self.browser.find_element(By.CSS_SELECTOR, 'button[type="submit"]')
        self.browser.execute_script("arguments[0].click();", submit_button)

    async def _do_fetch_with_browser(self, url, app_details):
        def _has_recaptcha(browser):
            # captcha = browser.find_elements(
            #    By.CSS_SELECTOR, "iframe[name^='a-'][src^='https://www.google.com/recaptcha/api2/anchor?']"
            # )
            # return bool(captcha)
            # olegeech: never caught captcha for now, so this is just a stub for now
            return False

        def _save_page_source(browser):
            # save page source in case of issues
            if not os.path.exists(OUTPUT_DIR):
                os.makedirs(OUTPUT_DIR)
            out_file = f"{OUTPUT_DIR}/{app_details['number']}-{app_details['type']}-{app_details['year']}.html"
            page_source = browser.page_source
            if page_source:
                with open(out_file, "w") as f:
                    f.write(page_source)

        self.app_details = app_details
        browser = self._get_browser()
        application_status_text = None

        try:
            browser.get(url)
            WebDriverWait(browser, PAGE_LOAD_LIMIT_SECONDS).until(
                lambda x: _has_recaptcha(x) or x.find_element(By.CLASS_NAME, "wrapper__form")
            )

            if _has_recaptcha(browser):
                logger.warning("Recaptcha has been hit, solve it please to continue")
                WebDriverWait(browser, CAPTCHA_WAIT_SECONDS).until(lambda x: x.find_element(By.CLASS_NAME, "wrapper__form"))

            self._submit_form(app_details)

            WebDriverWait(browser, PAGE_LOAD_LIMIT_SECONDS).until(
                lambda x: _has_recaptcha(x) or x.find_element(By.CLASS_NAME, "alert__content")
            )

            application_status = browser.find_element_by_class_name("alert__content")
            application_status_text = application_status.get_attribute("innerHTML")
            self._log(logging.INFO, "Application status fetched")

        except (WebDriverException, MaxRetryError) as err:
            self._log(logging.ERROR, "An error has occurred during page loading: %s", err)
            _save_page_source(browser)
            self.close()

        return application_status_text

    async def fetch(self, url, app_details):
        """
        Fetches page with retries
        """
        res = await self._do_fetch_with_browser(url=url, app_details=app_details)
        attempts_left = self.retries
        while attempts_left and not res:
            attempts_left -= 1
            retry_in = int(RETRY_INTERVAL / 3 + random.randint(1, int(2 * RETRY_INTERVAL / 3)))
            self._log(logging.WARNING, "Fetch failed, retrying %s later in %d seconds", url, retry_in)
            await asyncio.sleep(retry_in)
            res = await self._do_fetch_with_browser(url=url, app_details=app_details)
        return res

    def close(self):
        if self.browser:
            self.browser.quit()
            self.browser = None
        if self.display:
            self.display.stop()
            self.display = None
