import unittest
import asyncio
import logging
from fetcher.browser import Browser

URL = "https://frs.gov.cz/informace-o-stavu-rizeni/"

# Setting up the logger
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Test case data
test_data = [
    {"number": 27628, "type": "DP", "suffix": "0", "year": 2023},
    {"number": 43075, "type": "DP", "suffix": "0", "year": 2023},
    {"number": 57638, "type": "ZM", "suffix": "5", "year": 2023},
    {"number": 17271, "type": "DP", "suffix": "4", "year": 2023},
    {"number": 40465, "type": "DP", "suffix": "4", "year": 2023},
    {"number": 40018, "type": "DP", "suffix": "0", "year": 2023},
    {"number": 15904, "type": "TP", "suffix": "0", "year": 2023},
    {"number": 40139, "type": "DP", "suffix": "2", "year": 2023},
    {"number": 20174, "type": "DP", "suffix": "3", "year": 2023},
    {"number": 26394, "type": "DP", "suffix": "0", "year": 2023},
    {"number": 6996, "type": "DV", "suffix": "0", "year": 2023},
    {"number": 27774, "type": "DP", "suffix": "0", "year": 2023},
    {"number": 24574, "type": "DP", "suffix": "3", "year": 2023},
    {"number": 65199, "type": "ZM", "suffix": "5", "year": 2023},
    {"number": 40110, "type": "DP", "suffix": "0", "year": 2023},
    {"number": 19506, "type": "TP", "suffix": "0", "year": 2023},
    {"number": 28743, "type": "DP", "suffix": "0", "year": 2023},
    {"number": 23155, "type": "TP", "suffix": "0", "year": 2023},
    {"number": 34208, "type": "DP", "suffix": "0", "year": 2023},
    {"number": 38703, "type": "DP", "suffix": "0", "year": 2023},
]


class TestBrowserLoad(unittest.TestCase):
    def setUp(self):
        # Create browser object
        self.browser = Browser()

    def test_load(self):
        # Define an async function to handle the load test
        async def run_test():
            tasks = []
            for app_details in test_data:
                task = asyncio.ensure_future(self.browser.fetch(URL, app_details))
                tasks.append(task)

            results = await asyncio.gather(*tasks)

            # Here, results is a list of all outputs from the browser.fetch method for each app_details
            for result, app_details in zip(results, test_data):
                logger.info(f"Result for {app_details['number']}: {result}")
                # Add checks here to see if you get the expected results or behaviors
                self.assertIsNotNone(result)

        # Run the async function
        asyncio.run(run_test())

    def tearDown(self):
        self.browser.close()


if __name__ == "__main__":
    unittest.main()
