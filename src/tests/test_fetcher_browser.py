"""Unit tests for fetcher Selenium session-dead recovery"""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _install_selenium_stubs():
    """Stub selenium/pyvirtualdisplay so browser.py imports without fetcher venv deps"""
    if "selenium" in sys.modules and hasattr(sys.modules["selenium"], "webdriver"):
        return

    class InvalidSessionIdException(Exception):
        pass

    class WebDriverException(Exception):
        pass

    class TimeoutException(WebDriverException):
        pass

    class NoSuchElementException(WebDriverException):
        pass

    class ElementClickInterceptedException(WebDriverException):
        pass

    selenium = ModuleType("selenium")
    webdriver = ModuleType("selenium.webdriver")
    webdriver.firefox = SimpleNamespace(options=SimpleNamespace(Options=MagicMock))
    common = ModuleType("selenium.common")
    exceptions = ModuleType("selenium.common.exceptions")
    exceptions.WebDriverException = WebDriverException
    exceptions.ElementClickInterceptedException = ElementClickInterceptedException
    exceptions.TimeoutException = TimeoutException
    exceptions.NoSuchElementException = NoSuchElementException
    exceptions.InvalidSessionIdException = InvalidSessionIdException
    by = ModuleType("selenium.webdriver.common.by")
    by.By = MagicMock()
    support_wait = ModuleType("selenium.webdriver.support.wait")
    support_wait.WebDriverWait = MagicMock()
    support_ec = ModuleType("selenium.webdriver.support.expected_conditions")
    support = ModuleType("selenium.webdriver.support")
    action_chains = ModuleType("selenium.webdriver.common.action_chains")
    action_chains.ActionChains = MagicMock()
    common_pkg = ModuleType("selenium.webdriver.common")

    sys.modules["selenium"] = selenium
    sys.modules["selenium.webdriver"] = webdriver
    sys.modules["selenium.common"] = common
    sys.modules["selenium.common.exceptions"] = exceptions
    sys.modules["selenium.webdriver.common"] = common_pkg
    sys.modules["selenium.webdriver.common.by"] = by
    sys.modules["selenium.webdriver.common.action_chains"] = action_chains
    sys.modules["selenium.webdriver.support"] = support
    sys.modules["selenium.webdriver.support.wait"] = support_wait
    sys.modules["selenium.webdriver.support.expected_conditions"] = support_ec
    sys.modules["pyvirtualdisplay"] = SimpleNamespace(Display=MagicMock)
    sys.modules["fake_useragent"] = SimpleNamespace(UserAgent=MagicMock)
    sys.modules["bs4"] = SimpleNamespace(BeautifulSoup=MagicMock)


_install_selenium_stubs()

from selenium.common.exceptions import (  # noqa: E402
    InvalidSessionIdException,
    TimeoutException,
    WebDriverException,
)
from fetcher.browser import Browser, is_session_dead  # noqa: E402


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (WebDriverException("Tried to run command without establishing a connection"), True),
        (WebDriverException("Failed to decode response from marionette"), True),
        (InvalidSessionIdException("invalid session id"), True),
        (WebDriverException("Browsing context has been discarded"), True),
        (TimeoutException("Application submit form wasn't found in the HTML"), False),
        (WebDriverException("Status field wasn't found"), False),
        (Exception("unrelated boom"), False),
    ],
)
def test_is_session_dead(exc, expected):
    assert is_session_dead(exc) is expected


class MockDriver:
    def __init__(self, quit_error=None):
        self.quit_error = quit_error
        self.quit_calls = 0

    def quit(self):
        self.quit_calls += 1
        if self.quit_error:
            raise self.quit_error


class MockDisplay:
    def __init__(self, stop_error=None):
        self.stop_error = stop_error
        self.stop_calls = 0

    def stop(self):
        self.stop_calls += 1
        if self.stop_error:
            raise self.stop_error


def _browser_with_driver():
    browser = Browser()
    browser.app_details = {"number": "1"}
    driver = MockDriver()
    display = MockDisplay()
    browser.browser = driver
    browser.display = display
    return browser, driver, display


def test_close_clears_refs_when_quit_fails():
    browser = Browser()
    browser.app_details = {"number": "1"}
    driver = MockDriver(
        quit_error=WebDriverException("Tried to run command without establishing a connection")
    )
    display = MockDisplay(stop_error=RuntimeError("display already gone"))
    browser.browser = driver
    browser.display = display

    browser.close()

    assert browser.browser is None
    assert browser.display is None
    assert driver.quit_calls == 1
    assert display.stop_calls == 1


def test_soft_error_keeps_warm_browser():
    browser, driver, display = _browser_with_driver()

    browser._handle_browser_failure(TimeoutException("Application submit form wasn't found in the HTML"))

    assert browser.browser is driver
    assert browser.display is display
    assert browser._consecutive_session_dead == 0
    assert driver.quit_calls == 0


def test_session_dead_closes_and_counts():
    browser, driver, _display = _browser_with_driver()

    browser._handle_browser_failure(
        WebDriverException("Tried to run command without establishing a connection")
    )

    assert browser.browser is None
    assert browser._consecutive_session_dead == 1
    assert driver.quit_calls == 1


def test_session_dead_exits_after_threshold():
    browser, driver, _display = _browser_with_driver()
    browser._consecutive_session_dead = 1

    with pytest.raises(SystemExit) as exc_info:
        browser._handle_browser_failure(
            WebDriverException("Failed to decode response from marionette")
        )

    assert exc_info.value.code == 1
    assert browser.browser is None
    assert driver.quit_calls == 1


def test_fetch_success_resets_session_dead_counter():
    browser = Browser()
    browser._consecutive_session_dead = 2
    browser._note_fetch_success()
    assert browser._consecutive_session_dead == 0


def test_safe_save_page_source_swallows_dead_driver_errors(tmp_path, monkeypatch):
    monkeypatch.setattr("fetcher.browser.OUTPUT_DIR", str(tmp_path))
    browser = Browser()
    browser.app_details = {"number": "1"}

    class DeadDriver:
        @property
        def page_source(self):
            raise WebDriverException("Tried to run command without establishing a connection")

    browser._safe_save_page_source(
        DeadDriver(), {"number": "1", "type": "TP", "year": 2026}
    )
    assert list(tmp_path.iterdir()) == []
