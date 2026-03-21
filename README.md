# MVČR Application Status Notifier

A Telegram bot that monitors Czech Ministry of Interior (MVČR) immigration application statuses and notifies you of any changes. Supports both **OAM** (residency applications filed in Czech Republic) and **ŽOV** (visa applications submitted at Czech embassies abroad).

**[Open the bot in Telegram](https://t.me/mvcr_status_rizeni_2024_bot)** -- subscribe with your application number and get notified when the status changes.

<img src="https://private-user-images.githubusercontent.com/21361354/567182652-0c95255b-d846-43e5-83f6-64d203d88b50.png?jwt=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJnaXRodWIuY29tIiwiYXVkIjoicmF3LmdpdGh1YnVzZXJjb250ZW50LmNvbSIsImtleSI6ImtleTUiLCJleHAiOjE3NzQwNTg0NDksIm5iZiI6MTc3NDA1ODE0OSwicGF0aCI6Ii8yMTM2MTM1NC81NjcxODI2NTItMGM5NTI1NWItZDg0Ni00M2U1LTgzZjYtNjRkMjAzZDg4YjUwLnBuZz9YLUFtei1BbGdvcml0aG09QVdTNC1ITUFDLVNIQTI1NiZYLUFtei1DcmVkZW50aWFsPUFLSUFWQ09EWUxTQTUzUFFLNFpBJTJGMjAyNjAzMjElMkZ1cy1lYXN0LTElMkZzMyUyRmF3czRfcmVxdWVzdCZYLUFtei1EYXRlPTIwMjYwMzIxVDAxNTU0OVomWC1BbXotRXhwaXJlcz0zMDAmWC1BbXotU2lnbmF0dXJlPWNkNmIyMDNjYzAzZGMzMTIxOTIxMDUyM2VmMjMwM2Y1MjIzZGE2MGFjNTBmZWRjYzQyY2RjNzZlY2IxZDg5NWEmWC1BbXotU2lnbmVkSGVhZGVycz1ob3N0In0.EDYzjilm908nwP8zeQtcVtQETCr0VP28y0b7RwF1slQ" alt="Bot Screenshot" width="640" />

## Bot Features

- 🔄 **Automated Status Checks**: The bot checks your application status every 60 minutes and sends updates if there are any changes.
- 📌 **Subscription Management**: Start tracking your application with the `/subscribe` command.
- 🔍 **Current Status Check**: Use `/status` to get the current status of your application at any time.
- 🚀 **Force Refresh**: Need an immediate update? Use `/force_refresh` (limited to five uses per day for load management). ⏰
- 🌐 **Language Support**: Change the bot's language with the `/lang` command to suit your preference.
- ❌ **Unsubscribe**: Stop tracking your application using the `/unsubscribe` command.
- ⏰ **Custom Reminders**: Set specific times for reminders using the `/reminder` command for forced updates, ensuring you're informed at the most convenient times.

## Development

### Quick start

```bash
make env          # copy sample env files
make ssl          # generate self-signed certs for RabbitMQ TLS
make test         # run tests
make lint         # run ruff linter
make up           # build and start all services (postgres + rabbitmq + bot + fetcher)
make logs         # tail logs
make help         # list all available targets
```

### Architecture

For a deep dive into the system design - components, state machines, message flows, database schema, and more - see [ARCHITECTURE.md](ARCHITECTURE.md).

The project consists of two main services communicating via RabbitMQ:

**Telegram Bot** (`src/bot/`) - handles user interactions, stores subscriptions in PostgreSQL, and periodically queues status check requests.

**Fetcher** (`src/fetcher/`) - consumes requests from RabbitMQ, uses Selenium + Firefox to check application statuses on [ipc.gov.cz](https://ipc.gov.cz), and publishes results back. Supports horizontal scaling with multiple instances.

Both services connect to RabbitMQ over mutual TLS.

## Credits

Big thanks to [Inessa Vasilevskaya](https://github.com/fernflower) for her major contributions to this project.
Thanks to Fedir "Theo" L. (<https://theodorthegreathe.mojeid.cz/>) for providing Ukraine translations.

## Contributing

1. Fork the repository.
2. Create a new branch for your feature or bugfix.
3. Commit your changes with descriptive messages.
4. Submit a pull request for review.
