from selenium import webdriver
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
import time
import json
import pytest
from selenium.common.exceptions import (
    ElementClickInterceptedException,
)
import random
import fake_useragent
import logging

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


test_data = [
    {"number": 27628, "type": "DP", "suffix": "0", "year": 2023},
    {"number": 43075, "type": "DP", "suffix": "0", "year": 2023},
    {"number": 57638, "type": "ZM", "suffix": "5", "year": 2023},
    {"number": 17271, "type": "DP", "suffix": "4", "year": 2023},
    {"number": 40465, "type": "DP", "suffix": "4", "year": 2023},
]


class TestMVCR:
    def random_sleep(self, min_seconds=0.5, max_seconds=1.5):
        """Sleep for a random amount of time"""
        time.sleep(random.uniform(min_seconds, max_seconds))

    def type_with_delay(self, element, text, min_delay=0.05, max_delay=0.15):
        """Type text into an element one character at a time with a delay"""
        for char in text:
            element.send_keys(char)
            self.random_sleep(min_delay, max_delay)

    def set_random_resolution(self):
        """Pick one of popular resolutions"""
        resolutions = [(1936, 1056), (1920, 1080), (1366, 768), (1440, 900), (1420, 1080), (1600, 900)]
        chosen_resolution = random.choice(resolutions)
        self.driver.set_window_size(chosen_resolution[0], chosen_resolution[1])

    def clean_cookies(self, cookies):
        for cookie in cookies:
            samesite = cookie.get("sameSite", "None")
            if samesite:
                samesite = samesite.capitalize()
            if samesite not in ["None", "Lax", "Strict"]:
                cookie["sameSite"] = "None"
            else:
                cookie["sameSite"] = samesite
        logger.debug(f"Cookies: {cookies}")
        return cookies

    def load_cookies(self, filepath="all_cookies.json"):
        """Load cookies from a file and add them to the Selenium browser"""
        with open(filepath, "r") as file:
            cookies = json.load(file)
            cleaned_cookies = self.clean_cookies(cookies)
            for cookie in cleaned_cookies:
                self.driver.add_cookie(cookie)

    def setup_method(self, method):
        useragent = fake_useragent.UserAgent(browsers=["firefox"]).random
        # chrome_options = webdriver.ChromeOptions()
        # chrome_options.add_argument(f"user-agent={user_agent}")
        # self.driver = webdriver.Chrome(options=chrome_options)

        # configure display & options
        options = webdriver.firefox.options.Options()
        options.set_preference("intl.accept_languages", "cs-CZ")
        options.set_preference("http.response.timeout", 10)
        options.set_preference("general.useragent.override", useragent)
        options.set_preference("dom.webdriver.enabled", False)
        options.set_preference("useAutomationExtension", False)
        self.driver = webdriver.Firefox(options=options)
        self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        self.vars = {}

    def teardown_method(self, method):
        self.driver.quit()

    @pytest.mark.parametrize("data", test_data)
    def test_MVCR(self, data):
        number = str(data["number"])
        suffix = data["suffix"]
        type = data["type"]
        year = str(data["year"])

        self.set_random_resolution()
        self.driver.get("https://frs.gov.cz/informace-o-stavu-rizeni/")
        self.load_cookies("mvcr_cookies.json")
        WebDriverWait(self.driver, 10).until(
            lambda x: x.find_element(By.CLASS_NAME, "wrapper__form"),
            message="Body didn't load in time",
        )
        cookies = self.driver.find_element_by_xpath('//button[@class="button button__primary" and text()="Souhlasím se všemi"]')
        try:
            cookies.click()
        except ElementClickInterceptedException:
            pass

        input_field = self.driver.find_element(By.XPATH, "//input[@placeholder='12345']")
        input_field.click()
        self.random_sleep()
        self.type_with_delay(input_field, number)

        input_field_xx = self.driver.find_element(By.XPATH, "//input[@placeholder='XX']")
        input_field_xx.click()
        self.random_sleep()
        self.type_with_delay(input_field_xx, suffix)

        # Trigger type dropdown menu to appear
        menu1 = self.driver.find_element_by_xpath(
            "//div[contains(@class, 'react-select') and ancestor::div[contains(@style, 'width: 140px;')]]"
        )
        menu1.find_element_by_xpath("//div[contains(@class, 'react-select__control')]").click()

        time.sleep(0.5)
        # Locate and select the type dropdown by placeholder
        scroll1 = self.driver.find_element_by_xpath("//div[contains(@class, 'react-select__menu')]")
        scroll1.find_element_by_xpath(f".//div[text()='{type}']").click()

        # Trigger year dropdown menu to appear
        menu2 = self.driver.find_element_by_xpath(
            "//div[contains(@class, 'react-select') and ancestor::div[contains(@style, 'width: 100px;')]]"
        )
        menu2.find_element_by_xpath(".//div[contains(@class, 'react-select__control')]").click()

        self.random_sleep()
        # Locate and select the year dropdown by placeholder
        scroll2 = menu2.find_element_by_xpath(".//div[contains(@class, 'react-select__menu')]")
        scroll2.find_element_by_xpath(f".//div[text()='{year}']").click()

        # Click submit
        submit_button = self.driver.find_element(By.CSS_SELECTOR, ".button--large")
        self.driver.execute_script("arguments[0].scrollIntoView();", submit_button)
        actions = ActionChains(self.driver)
        actions.move_to_element(submit_button).perform()
        self.random_sleep()
        self.driver.execute_script("arguments[0].click();", submit_button)
        self.driver.execute_script("window.scrollTo(0, 0);")

        # submit_button.click()

        WebDriverWait(self.driver, 5).until(
            lambda x: x.find_element(By.CLASS_NAME, "alert__content"),
            message="Status field wasn't found",
        )
        self.driver.execute_script("window.scrollBy(0, -document.body.scrollHeight);")
        time.sleep(5)
