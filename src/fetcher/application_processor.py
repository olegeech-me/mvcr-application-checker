import json
import logging
import sys

logger = logging.getLogger(__name__)


class ApplicationProcessor:
    def __init__(self, messaging, browser, url):
        self.messaging = messaging
        self.browser = browser
        self.url = url
        self.current_message = None

    def process_message(self, ch, method, body):
        app_details = json.loads(body.decode("utf-8"))
        logger.info(f"Received application details {app_details}")

        app_status = self.browser.fetch(self.url, app_details)
        if app_status:
            logger.info(f"Successfully fetched status for application number {app_details['number']}")
            app_details["status"] = app_status
            self.messaging.send_message("StatusUpdateQueue", app_details)
            ch.basic_ack(delivery_tag=method.delivery_tag)  # Acknowledge the message
            logger.debug("Message was pushed to StateUpdateQueue")
        else:
            logger.error(f"Failed to fetch status for application number {app_details['number']}")
            ch.basic_nack(delivery_tag=method.delivery_tag)  # NACK the message

    def callback(self, ch, method, properties, body):
        self.current_message = method.delivery_tag
        try:
            self.process_message(ch, method, body)
        finally:
            self.current_message = None

    def shutdown(self):
        if self.current_message:
            logger.info(f"NACK'ing message with delivery_tag: {self.current_message}")
            self.messaging.channel.basic_nack(delivery_tag=self.current_message)
        self.messaging.close()
        self.browser.close()
        sys.exit(0)
