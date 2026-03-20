"""
Standalone test: fetch ZOV status via Browser class against ipc.gov.cz

Validates that:
- The IPC form accepts ZOV-only submissions (OAM fields left empty)
- The alert__content selector works for ZOV responses
- Exact status text for each known ZOV number

Run:
    python -m pytest src/tests/test_zov_browser.py -v -s
    cd src && python -m tests.test_zov_browser
"""
import unittest
import asyncio
import logging
from fetcher.browser import Browser
from fetcher.config import URL

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

zov_test_data = [
    {
        "number": "ISTA202504220001",
        "source": "zov",
        "suffix": "0",
        "type": "ZOV",
        "year": "0",
        "expected_category": "approved",
        "expected_keyword": "preliminarily assessed positively",
    },
    {
        "number": "ISTA202410300005",
        "source": "zov",
        "suffix": "0",
        "type": "ZOV",
        "year": "0",
        "expected_category": "denied",
        "expected_keyword": "rejected",
    },
    {
        "number": "ISTA202601150003",
        "source": "zov",
        "suffix": "0",
        "type": "ZOV",
        "year": "0",
        "expected_category": "in_progress",
        "expected_keyword": "being processed",
    },
    {
        "number": "ZZZZ000000000000",
        "source": "zov",
        "suffix": "0",
        "type": "ZOV",
        "year": "0",
        "expected_category": "not_found",
        "expected_keyword": "not found",
    },
]


class TestZOVBrowser(unittest.TestCase):
    def setUp(self):
        self.browser = Browser()

    def test_zov_fetch(self):
        async def run_test():
            for app_details in zov_test_data:
                number = app_details["number"]
                expected_kw = app_details["expected_keyword"]
                logger.info(
                    "Testing ZOV: %s (expected: %s)",
                    number,
                    app_details["expected_category"],
                )
                result = await self.browser.fetch(URL, app_details)
                print(f"=== {number} ===")
                print(result)
                print()
                self.assertIsNotNone(
                    result,
                    f"Failed to fetch status for {number}",
                )
                self.assertIn(
                    expected_kw,
                    result.lower(),
                    f"Expected keyword '{expected_kw}' not in response for {number}",
                )

        asyncio.run(run_test())

    def tearDown(self):
        self.browser.close()


if __name__ == "__main__":
    unittest.main()
