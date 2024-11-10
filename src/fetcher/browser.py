"""
Use selenium browser to interact with website

Ideas borrowed from https://github.com/fernflower/trvalypobytexamchecker/blob/main/src/fetcher/a2exams_fetcher.py
"""

import logging
import asyncio
import random
import os
import time
import json
import hashlib
from bs4 import BeautifulSoup
from pyvirtualdisplay import Display
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    WebDriverException,
    ElementClickInterceptedException,
    TimeoutException,
    NoSuchElementException,
)
from selenium.webdriver.common.action_chains import ActionChains
import fake_useragent

from fetcher.config import PAGE_LOAD_LIMIT_SECONDS, CAPTCHA_WAIT_SECONDS, OUTPUT_DIR, RETRY_INTERVAL

logger = logging.getLogger(__name__)


class CustomMaxRetryError(Exception):
    def __init__(self, url, msg):
        super().__init__(msg)
        self.url = url


class Browser:
    def __init__(self, retries=3):
        self.display = None
        self.browser = None
        self.useragent = None
        self.retries = retries
        self.app_details = {}

    def _log(self, log_level, message, *args):
        """Wrapper around logger to add application number to the log messages."""
        msg = f"[{self.app_details['number']}] {message}"
        logger.log(log_level, msg, *args)

    def _set_useragent(self):
        useragent = fake_useragent.UserAgent(browsers=["firefox"]).random
        self._log(logging.INFO, "User-Agent for this session will be %s", useragent)
        self.useragent = useragent

    def _get_ua_hash(self):
        """Return a hash of the user-agent string"""
        return hashlib.sha256(self.useragent.encode("utf-8")).hexdigest()

    def save_cookies(self):
        """Save cookies to a file"""
        cookies = self.browser.get_cookies()
        if not os.path.exists("cookies"):
            os.makedirs("cookies")
        ua_hash = self._get_ua_hash()
        with open(f'cookies/{ua_hash}.json', 'w') as f:
            json.dump(cookies, f)

    def load_cookies(self):
        """Load cookies from a file and add them to the Selenium browser"""
        ua_hash = self._get_ua_hash()
        cookie_file = f'cookies/{ua_hash}.json'
        if os.path.exists(cookie_file):
            self._log(logging.INFO, "Found cookies for the current User-Agent, loading from %s", cookie_file)
            with open(cookie_file, 'r') as f:
                cookies = json.load(f)
                for cookie in cookies:
                    self.browser.add_cookie(cookie)

    def clean_html(self, html_content):
        """Filter out unsupported TG html tags"""
        # https://core.telegram.org/bots/api#html-style
        allowed_tags = {
            'b': [],
            'strong': [],
            'i': [],
            'em': [],
            'u': [],
            'ins': [],
            's': [],
            'strike': [],
            'del': [],
            'span': ['class'],
            'tg-spoiler': [],
            'a': ['href'],
            'code': ['class'],
            'pre': [],
            'blockquote': ['expandable'],
            'tg-emoji': ['emoji-id'],
        }

        soup = BeautifulSoup(html_content, 'html.parser')

        # Replace <br> tags with newline characters
        for br in soup.find_all('br'):
            br.replace_with('\n')

        # Remove unwanted tags and attributes
        for tag in soup.find_all():
            if tag.name not in allowed_tags:
                tag.unwrap()
            else:
                # Clean attributes
                allowed_attrs = allowed_tags[tag.name]
                attrs = dict(tag.attrs)
                for attr in attrs:
                    if attr not in allowed_attrs:
                        del tag.attrs[attr]
                # Additional check for <span class="tg-spoiler">
                if tag.name == 'span':
                    if 'class' in tag.attrs and 'tg-spoiler' in tag['class']:
                        continue
                    else:
                        tag.unwrap()
                # Additional check for <code class="language-...">
                if tag.name == 'code' and 'class' in tag.attrs:
                    classes = tag['class']
                    if not any(cls.startswith('language-') for cls in classes):
                        del tag.attrs['class']

        return str(soup)

    def _init_browser(self):
        # set user-agent
        self._set_useragent()
        # configure display & options
        resolution = self.set_random_resolution()
        self.display = Display(visible=0, size=resolution)
        self.display.start()
        self._log(logging.INFO, "Initialized virtual display")
        options = webdriver.firefox.options.Options()
        options.set_preference("intl.accept_languages", "cs-CZ")
        options.set_preference("http.response.timeout", PAGE_LOAD_LIMIT_SECONDS)
        options.set_preference("general.useragent.override", self.useragent)
        options.set_preference("dom.webdriver.enabled", False)
        options.set_preference("useAutomationExtension", False)
        options.headless = False
        self.browser = webdriver.Firefox(options=options)
        self.browser.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        self.load_cookies()

    def _get_browser(self, force=False):
        if not force and self.browser:
            return self.browser
        self._init_browser()
        return self.browser

    def random_sleep(self, min_seconds=0.5, max_seconds=1.5):
        """Sleep for a random amount of time"""
        time.sleep(random.uniform(min_seconds, max_seconds))

    def type_with_delay(self, element, text, min_delay=0.05, max_delay=0.15):
        """Type text into an element one character at a time with a delay"""
        for char in str(text):
            element.send_keys(char)
            self.random_sleep(min_delay, max_delay)

    def set_random_resolution(self):
        """Pick one of popular resolutions"""
        # resolutions = [(1936, 1056), (1920, 1080), (1366, 768), (1440, 900), (1420, 1080), (1600, 900)]
        # chosen_resolution = random.choice(resolutions)
        # olegeech: for now, just use the most popular resolution
        chosen_resolution = (1420, 1080)
        self._log(logging.INFO, "Setting resolution to %s", chosen_resolution)
        return chosen_resolution

    def _submit_form(self, app_details):
        """Submit application details into the form"""
        logged_details = {key: app_details[key] for key in ["number", "suffix", "type", "year"]}
        self._log(logging.INFO, "Submitting application data %s", logged_details)

        WebDriverWait(self.browser, PAGE_LOAD_LIMIT_SECONDS).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".input__control"))
        )

        # Try clicking on cookies button
        cookies = self.browser.find_element_by_xpath('//button[@class="button button__primary" and text()="Souhlasím se všemi"]')
        try:
            cookies.click()
            self._log(logging.INFO, "Cookies button found, clicked.")
        except ElementClickInterceptedException:
            self._log(logging.INFO, "Cookies button is not active")

        # Locate and fill out the application number field by its placeholder
        application_number_field = self.browser.find_element(By.NAME, "proceedings.referenceNumber")
        self.random_sleep()
        application_number_field.clear()
        self.type_with_delay(application_number_field, app_details["number"])

        # Locate and fill out the application type field by its placeholder
        application_suffix_field = self.browser.find_element(By.NAME, "proceedings.additionalSuffix")
        application_suffix_field.clear()
        self.random_sleep()
        self.type_with_delay(application_suffix_field, app_details["suffix"])

        # Select the "Type" from the dropdown
        try:
            # Locate the select wrapper containing 'proceedings.category'
            type_select_wrapper = self.browser.find_element(
                By.XPATH,
                "//div[contains(@class, 'select__wrapper') and .//input[@name='proceedings.category']]"
            )
            # Find the 'react-select__control' inside the wrapper
            type_dropdown = type_select_wrapper.find_element(
                By.XPATH,
                ".//div[contains(@class, 'react-select__control')]"
            )
            self.random_sleep()
            type_dropdown.click()

            # Wait for the options to be present
            WebDriverWait(self.browser, 3).until(
                EC.presence_of_element_located((By.CLASS_NAME, "react-select__menu"))
            )

            # Adjusted XPath to locate the option correctly
            type_option = self.browser.find_element(
                By.XPATH,
                f"//div[contains(@class, 'react-select__option') and .//div[normalize-space(text())='{app_details['type']}']]"
            )

            # Scroll the option into view and click it using JavaScript
            self.browser.execute_script("arguments[0].scrollIntoView(true);", type_option)
            self.random_sleep()
            self.browser.execute_script("arguments[0].click();", type_option)

        except (WebDriverException, CustomMaxRetryError, TimeoutException):
            self._log(logging.ERROR, "Error waiting for Type list to appear")
            self.close()
        except Exception as e:
            self._log(logging.ERROR, "Error selecting 'Type': %s", e)
            self.close()
            raise

        # Select the "Year" from the dropdown
        try:
            # Locate the select wrapper containing 'proceedings.year'
            year_select_wrapper = self.browser.find_element(
                By.XPATH,
                "//div[contains(@class, 'select__wrapper') and .//input[@name='proceedings.year']]"
            )
            # Find the 'react-select__control' inside the wrapper
            year_dropdown = year_select_wrapper.find_element(
                By.XPATH,
                ".//div[contains(@class, 'react-select__control')]"
            )
            self.random_sleep()

            # Scroll the year_dropdown into view before clicking it
            self.browser.execute_script("arguments[0].scrollIntoView({block: 'center'});", year_dropdown)
            self.random_sleep()
            # Click the year_dropdown to open the menu
            year_dropdown.click()

            # Wait for the menu to be present
            WebDriverWait(self.browser, 3).until(
                EC.presence_of_element_located((By.CLASS_NAME, "react-select__menu"))
            )

            # Locate the desired option
            year_option = self.browser.find_element(
                By.XPATH,
                f"//div[contains(@class, 'react-select__option') and .//div[normalize-space(text())='{app_details['year']}']]"
            )

            # Scroll the year_option into view before clicking it
            self.browser.execute_script("arguments[0].scrollIntoView({block: 'center'});", year_option)
            self.random_sleep()
            # Click the year_option
            self.browser.execute_script("arguments[0].click();", year_option)


        except (WebDriverException, CustomMaxRetryError, TimeoutException):
            self._log(logging.ERROR, "Error waiting for year list to appear")
            self.close()
        except Exception as e:
            self._log(logging.ERROR, "Error selecting 'Year': %s", e)
            self.close()
            raise

        # Locate the submit button and click it to submit the form
        submit_button = self.browser.find_element(By.CSS_SELECTOR, 'button[type="submit"]')
        self.random_sleep()
        self.browser.execute_script("arguments[0].scrollIntoView();", submit_button)
        actions = ActionChains(self.browser)
        actions.move_to_element(submit_button).perform()
        self.browser.execute_script("arguments[0].click();", submit_button)

    async def _do_fetch_with_browser(self, url, app_details):
        def _has_recaptcha(browser):
            # captcha = browser.find_elements(
            #    By.CSS_SELECTOR, "iframe[name^='a-'][src^='https://www.google.com/recaptcha/api2/anchor?']"
            # )
            # return bool(captcha)
            #
            # olegeech: MVCR website uses invisible recaptcha, so we can't detect it.
            # It can only be detected by the fact there is no results after submitting the form.
            # Also the POST request is being denyed with the statement "Recaptcha verification failed"
            # It's not possible to easily see the POST reply in Selenium, so we just check for the results.
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
        application_status = None
        application_status_text = None

        try:
            browser.get(url)
            WebDriverWait(browser, PAGE_LOAD_LIMIT_SECONDS).until(
                lambda x: _has_recaptcha(x) or x.find_element(By.CLASS_NAME, "wrapper__form"),
                message="Application submit form wasn't found in the HTML",
            )

            if _has_recaptcha(browser):
                logger.warning("Recaptcha has been hit, solve it please to continue")
                WebDriverWait(browser, CAPTCHA_WAIT_SECONDS).until(lambda x: x.find_element(By.CLASS_NAME, "wrapper__form"))

            # BUG: sometimes on some systems after submitting data
            # the page still appears as nothing was done
            # Magically, re-submitting data resolves the issue ...
            retry_count = 0
            for _attempt in range(3):
                self._submit_form(app_details)
                try:
                    WebDriverWait(browser, 5).until(
                        lambda x: x.find_element(By.CLASS_NAME, "alert__content"),
                        message="Status field wasn't found",
                    )
                    application_status = browser.find_element_by_class_name("alert__content")
                    break
                except (WebDriverException, NoSuchElementException, TimeoutException) as e:
                    retry_count += 1
                    self._log(logging.ERROR, f"Submit failed on attempt {retry_count}: {e}")
                    await asyncio.sleep(1)

            if application_status:
                application_status_text = application_status.get_attribute("innerHTML")
                self._log(logging.INFO, "Application status fetched")
                self.save_cookies()
                # Filter out / replace unsupported HTML tags
                application_status_text = self.clean_html(application_status_text)
            else:
                raise CustomMaxRetryError(url=url, msg="Couldn't fetch application status")

        except (WebDriverException, CustomMaxRetryError, TimeoutException) as err:
            self._log(logging.ERROR, "An error has occurred during page loading: %s", err)
            _save_page_source(browser)
            self.close()
        except Exception as e:
            self._log(logging.ERROR, "Unexpected exception: %s", e)
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
