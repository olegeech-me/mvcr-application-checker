import pika
import json
import logging

logger = logging.getLogger(__name__)


class Messaging:
    def __init__(self, host, user, password):
        self.host = host
        self.user = user
        self.password = password
        self.connection = None
        self.channel = None

    def connect(self):
        """Establish a connection to the message broker."""
        credentials = pika.PlainCredentials(self.user, self.password)
        self.connection = pika.BlockingConnection(pika.ConnectionParameters(host=self.host, credentials=credentials))
        self.channel = self.connection.channel()
        logger.info("Connected to the RabbitMQ server")

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
