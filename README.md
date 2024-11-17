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
- **Metrics Collection**: Gathers and reports metrics using a built-in module that logs the performance and health status of the bot, providing insights into user activity and system behavior.

### 2. Fetcher Module

#### Fetcher Features

- **Messaging**: This class listens to fetch/refresh queues on RabbitMQ.
- **Browser Class**: Leverages Selenium and Firefox to emulate user actions, thereby fetching or refreshing each application status.
- **Application Processor**: Employed for each new request to ensure streamlined and efficient data processing.
- **Metrics and Monitoring**: Integrated with a metrics collector to track the performance and operational state of the fetcher instances, including task completion time, error rates, and queue processing status.
- **Scalable Deployment**: Supports running multiple instances of the fetcher module for high availability and load distribution, ensuring resilience and reliability even during high demand or single-instance failure.
- **SSL Encryption**: Ensures secure data transfer between the bot and fetcher modules, as well as database and RabbitMQ communications.


## Getting Started

To start receiving notifications:

1. Open the Telegram app and search for [MVCR Status Řízení Bot](https://t.me/mvcr_status_rizeni_2024_bot).
2. Click on **Start** or send `/start` to initiate the conversation.
3. Follow the prompts to enter your application details.
4. The bot will notify you of any status updates.


<img src="https://private-user-images.githubusercontent.com/21361354/386916774-b7e62b15-7e82-46f4-b6f4-5c91d24b11b2.jpg?jwt=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJnaXRodWIuY29tIiwiYXVkIjoicmF3LmdpdGh1YnVzZXJjb250ZW50LmNvbSIsImtleSI6ImtleTUiLCJleHAiOjE3MzE4MTMxODcsIm5iZiI6MTczMTgxMjg4NywicGF0aCI6Ii8yMTM2MTM1NC8zODY5MTY3NzQtYjdlNjJiMTUtN2U4Mi00NmY0LWI2ZjQtNWM5MWQyNGIxMWIyLmpwZz9YLUFtei1BbGdvcml0aG09QVdTNC1ITUFDLVNIQTI1NiZYLUFtei1DcmVkZW50aWFsPUFLSUFWQ09EWUxTQTUzUFFLNFpBJTJGMjAyNDExMTclMkZ1cy1lYXN0LTElMkZzMyUyRmF3czRfcmVxdWVzdCZYLUFtei1EYXRlPTIwMjQxMTE3VDAzMDgwN1omWC1BbXotRXhwaXJlcz0zMDAmWC1BbXotU2lnbmF0dXJlPWM3ZTZkYmE5OTcwZTU2YWQ0YzQ4Yzc5MGE5NWVhMWUyMzgxYjg2ZmE4ZDQ2ZmZkMWY3NzgyNjVlZmZjNGU4ZGEmWC1BbXotU2lnbmVkSGVhZGVycz1ob3N0In0.cYOJI6WVAtSfo_Vfu1kmvSHWiPnt3p2aih4rPI_5FLo" alt="Bot Screenshot" width="220" />

## Bot Features

- 🔄 **Automated Status Checks**: The bot checks your application status every 60 minutes and sends updates if there are any changes.
- 📌 **Subscription Management**: Start tracking your application with the `/subscribe` command.
- 🔍 **Current Status Check**: Use `/status` to get the current status of your application at any time.
- 🚀 **Force Refresh**: Need an immediate update? Use `/force_refresh` (limited to five uses per day for load management). ⏰
- 🌐 **Language Support**: Change the bot's language with the `/lang` command to suit your preference.
- ❌ **Unsubscribe**: Stop tracking your application using the `/unsubscribe` command.
- ⏰ **Custom Reminders**: Set specific times for reminders using the `/reminder` command for forced updates, ensuring you're informed at the most convenient times.


## Acknowledgments

This project stands as a testament to open-source collaboration and the ongoing efforts to simplify and automate bureaucratic processes for foreigners in the Czech Republic.

## Credits

Big thanks to [Inessa Vasilevskaya](https://github.com/fernflower) for her major contributions to this project.
Thanks to Fedir "Theo" L. (<https://theodorthegreathe.mojeid.cz/>) for providing Ukraine translations.

## Contributing

We welcome contributions from the community! To contribute:

1. Fork the repository.
2. Create a new branch for your feature or bugfix.
3. Commit your changes with descriptive messages.
4. Submit a pull request for review.

Please read the [Development Guide](./docs/development.md) for more details.
