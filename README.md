# MVCR Residential Application Status Notifier

This project offers automation for the residential application status from the Ministry of Interior of the Czech Republic. It diligently tracks changes in status and immediately notifies users of any updates. Users can conveniently subscribe to these notifications via the Telegram bot [MVCR Status Řízení Bot](https://t.me/mvcr_status_rizeni_2024_bot). After providing their application details, the integrated monitor service will fetch and compare the application status at periodic intervals to keep users informed.

## Modules and Architecture

The project is architecturally divided into two primary modules:

### 1. Telegram Bot Module

#### Bot Features

- **Telegram Bot**: This consists of the main Telegram bot code equipped with various command handlers.
- **Database**: The bot is integrated with a PostgreSQL database to store user data and application details.
- **RabbitMQ Integration**: It's integrated with a RabbitMQ server, which aids in the generation and consumption of fetch/refresh requests and responses for the fetchers.
- **Application Monitor**: This feature periodically scans the database, generating refresh requests whenever an application's last timestamp exceeds the configured time period.

### 2. Fetcher Module

#### Fetcher Features

- **Messaging**: This class listens to fetch/refresh queues on RabbitMQ.
- **Browser Class**: Leverages Selenium and Firefox to emulate user actions, thereby fetching or refreshing each application status.
- **Application Processor**: Employed for each new request to ensure streamlined and efficient data processing.

The Fetcher module is engineered with scalability and resilience in mind. It's designed for multi-instance deployment, ensuring that even if one instance encounters issues, others can seamlessly continue the task. This design choice significantly minimizes service interruptions.

For enhanced security and data integrity, the RabbitMQ server (within the Bot module) and its client (within the Fetcher module) employ SSL certificates, ensuring encrypted traffic and safeguarded data.

## Getting Started

To make use of this service, simply visit the [Telegram Bot link](https://t.me/mvcr_status_rizeni_2024_bot) and follow the instructions to subscribe.

## Acknowledgments

This project stands as a testament to open-source collaboration and the ongoing efforts to simplify and automate bureaucratic processes for foreigners in the Czech Republic.

## Credits

Big thanks to [Inessa Vasilevskaya](https://github.com/fernflower) for her major contributions to this project.
Thanks to Fedir "Theo" L. (<https://theodorthegreathe.mojeid.cz/>) for providing Ukraine translations.

## Development

Please see the [Development Guide](./docs/development.md) for more information.
