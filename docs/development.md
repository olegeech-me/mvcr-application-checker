# Development Guide for MVCR Residential Application Status Notifier

The project is structured using a modular approach, allowing for scalable and maintainable code. Here's a comprehensive guide to help you understand and contribute to the project.

## Project Structure

```plaintext
mvcr-application-checker
│
├── Dockerfile_bot                # Dockerfile for the Telegram bot service
├── Dockerfile_fetcher            # Dockerfile for the Fetcher service
├── LICENSE                       # License information
├── README.md                     # Main project documentation
│
├── bot.env                       # Bot environment variables (Ensure it matches `bot.sample.env`)
├── fetcher.env                   # Fetcher environment variables (Ensure it matches `fetcher.sample.env`)
├── postgres.env                  # PostgreSQL environment variables
├── rabbit.env                    # RabbitMQ environment variables
│
├── conf                          # Configurations for RabbitMQ
│   └── rabbitmq-server.conf
│
├── db-init-scripts               # Database initialization scripts
│   └── init.sql
│
├── docker-compose-bot.yaml       # Docker Compose for the Telegram bot
├── docker-compose-fetcher.yaml   # Docker Compose for the Fetcher service
│
├── requirements-bot.txt          # Python dependencies for the bot
├── requirements-fetcher.txt      # Python dependencies for the fetcher
│
├── src                           # Source code
│   ├── bot                       # Bot module
│   │   ├── database.py           # Database utilities and operations for the bot
│   │   ├── handlers.py           # Telegram command handlers
│   │   ├── loader.py             # Loader utilities
│   │   ├── monitor.py            # Application monitor code
│   │   └── rabbitmq.py           # RabbitMQ utilities and operations for the bot
│   └── fetcher                   # Fetcher module
│       ├── application_processor.py # Application processing logic
│       ├── browser.py                # Selenium browser operations
│       ├── config.py                 # Fetcher configurations
│       └── messaging.py              # RabbitMQ utilities and operations for the fetcher
│
└── ssl                            # SSL certificates and keys for RabbitMQ
    ├── ca.crt
    ├── client.crt
    ├── client.key
    ├── server.crt
    └── server.key
```

## Setting up the Development Environment

1. **Clone the Repository**:

   ```bash
   git clone https://github.com/olegeech-me/mvcr-application-checker.git mvcr-application-checker
   cd mvcr-application-checker
   ```

2. **Environment Configuration**:
   - Make a copy of the `bot.sample.env`, `fetcher.sample.env`, `postgres.sample.env`, and `rabbit.sample.env` and remove the `.sample` from their names. Modify the configuration values according to your environment.

3. **Docker Compose Setup**:
   - To start the bot along with its dependent services (PostgreSQL and RabbitMQ), run:

     ```bash
     docker-compose -f docker-compose-bot.yaml up --build
     ```

   - To start the fetcher service, run:

     ```bash
     docker-compose -f docker-compose-fetcher.yaml up --build
     ```

4. **Database Initialization**:
   - The database will initialize automatically using the `init.sql` script when you run the PostgreSQL container for the first time.

5. **Accessing the Services**:
   - You would need to create a Telegram bot and obtain its API token though BotFather first. Refer to [BotFather](https://telegram.me/BotFather) for more information.
   - The Telegram bot will start automatically once the services are up.
   - You can access the RabbitMQ management console at `http://localhost:15672`.

Remember, to actively contribute or make changes, you'd ideally want to familiarize yourself with the codebase, the flow between modules, and test any changes locally before submitting a pull request.

## Contribution

For contribution guidelines, refer to any existing documentation on the repository, or consider establishing a CONTRIBUTING.md if not present. Ensure you adhere to the project's coding standards and submit appropriate tests alongside feature implementations.
