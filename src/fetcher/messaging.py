import pika
import time
import json
import logging
import ssl

MAX_RETRIES = 25  # maximum number of connection retries
RETRY_DELAY = 5  # delay (in seconds) between retries

logger = logging.getLogger(__name__)


class Messaging:
    def __init__(self, host, user, password):
        self.host = host
        self.user = user
        self.password = password
        self.port = 5672
        self.connection = None
        self.channel = None

    def _create_ssl_context(self, ssl_params):
        logger.info(f"Rabbit connection ssl_params: {ssl_params}")
        context = ssl.create_default_context(cafile=ssl_params["cafile"])
        context.load_cert_chain(certfile=ssl_params["certfile"], keyfile=ssl_params["keyfile"])
        context.verify_mode = ssl.CERT_REQUIRED
        return context

    def connect(self, ssl_params=None):
        """Establish a connection to the message broker."""
        credentials = pika.PlainCredentials(self.user, self.password)

        if ssl_params:
            ssl_context = self._create_ssl_context(ssl_params)
            ssl_options = pika.SSLOptions(ssl_context, self.host)
            self.port = ssl_params["ssl_port"]
        else:
            ssl_options = None

        conn_params = pika.ConnectionParameters(
            credentials=credentials,
            host=self.host,
            port=self.port,
            ssl_options=ssl_options,
        )

        for retry in range(1, MAX_RETRIES + 1):
            try:
                logger.info(f"Connecting to {self.host} ...")
                self.connection = pika.BlockingConnection(conn_params)
                self.channel = self.connection.channel()
                logger.info(f"Connected to the RabbitMQ server at {self.host}")
                break  # Exit the loop if connection is successful
            except pika.exceptions.AMQPConnectionError:
                if retry < MAX_RETRIES:
                    logger.warning(f"Connection attempt {retry} failed. Retrying in {RETRY_DELAY} seconds...")
                    time.sleep(RETRY_DELAY)
                else:
                    logger.error("Max retries reached. Could not connect to RabbitMQ.")
                    raise

    def setup_queues(self, *queues):
        """Declare necessary queues."""
        for queue in queues:
            self.channel.queue_declare(queue=queue, durable=True)

    def send_message(self, queue_name, message_body):
        """Send a message to the specified queue."""
        if not self.channel:
            raise Exception("No active channel. Make sure to connect first.")
        self.channel.basic_publish(exchange="", routing_key=queue_name, body=json.dumps(message_body))
        logger.info(f"Successfully published message to {queue_name}")

    def consume_messages(self, queue_name, callback_func):
        """Consume messages from the specified queue."""
        if not self.channel:
            raise Exception("No active channel. Make sure to connect first.")
        self.channel.basic_consume(queue=queue_name, on_message_callback=callback_func, auto_ack=False)
        logger.info(f"Subscribing for updates at {queue_name}")
        self.channel.start_consuming()

    def close(self):
        """Close the connection."""
        if self.channel:
            logger.info("Closing RabbitMQ channel...")
            self.channel.close()
        if self.connection:
            logger.info("Closing RabbitMQ connection...")
            self.connection.close()
