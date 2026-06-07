# Agent Guide

MVČR application status notifier. Two independent Python services that talk over RabbitMQ:

- `src/bot/` — Telegram bot: user dialogs, PostgreSQL state, schedulers, notification delivery
- `src/fetcher/` — Selenium/Firefox worker: scrapes ipc.gov.cz, publishes results back

The services share no code. All source lives under `src/`; tests import with `PYTHONPATH=src`.

## Commands

Use the Makefile, not raw `docker compose` or `pytest`:

- `make lint` — ruff
- `make test` — full suite (verbose)
- `make test-quick` — full suite (summary)
- `make up` / `make down` — full stack; `make up-bot` / `make up-fetcher` for one side
- `make logs` — tail bot stack

Run a single test file directly when iterating: `PYTHONPATH=src .venv/bin/python -m pytest src/tests/test_handlers.py`.

## Module Map

Bot (`src/bot/`):

- `__main__.py` — entrypoint, handler registration, startup/shutdown
- `loader.py` — lazy singletons (bot, db, rabbit, dispatcher)
- `handlers.py` — Telegram commands and conversation dialogs
- `monitor.py` — background loops: `ApplicationMonitor`, `ReminderMonitor`, `NotificationDispatcher`
- `rabbitmq.py` — RabbitMQ adapter (connections, queues, publish, dedup, ack/nack)
- `processor.py` — business logic for consumed queue messages
- `database.py` — asyncpg access layer
- `fetcher_stats.py` — in-memory cache behind `/fetcher_stats`
- `prometheus_metrics.py`, `utils.py`, `texts/` (i18n)

Fetcher (`src/fetcher/`):

- `__main__.py` — entrypoint
- `application_processor.py` — fetch/refresh handling, retries
- `browser.py` — Selenium form filling and scraping
- `messaging.py` — RabbitMQ access layer
- `metrics_collector.py`, `prometheus_metrics.py`, `config.py`

## Where To Look

- `ARCHITECTURE.md` — system design, message flow, DB schema, state machine. Read it before changing message flow, DB state, queue handling, notification delivery, or deployment.
- `metrics.md` — exposed metrics and alert semantics.
- `deploy/mvcr-application-checker-helm/` — Helm chart.
- `db-init-scripts/init.sql` + `db-migrations/` — schema; migrations run on bot startup.

## Conventions

- Match surrounding code before adding any new pattern, abstraction, file, or class. Prefer small local helpers.
- Runtime knobs live in `config.py` with `os.getenv` defaults; mirror new ones in the sample `*.env` and Helm values.
- Use `logging`, never `print`.
- Comments are rare and explain intent, not mechanics; they do not end with a period.
- Classes and public methods get docstrings.
- For message/dict payloads, read required fields with `[]` and optional fields with `.get()`.
- All user-facing text goes through `message_texts` in all four languages (EN, RU, CZ, UA).
- Keep Prometheus label values bounded; never label with chat IDs, usernames, application numbers, raw status text, or raw exceptions.

## Tests

- pytest with async strict mode; reuse fixtures in `src/tests/conftest.py`.
- Add focused tests for new branches and failure/retry paths.
- Run `make lint` and the relevant tests before finishing a change.

## Releases

- Keep the version aligned across `src/*/config.py` defaults, README image tags, Helm `appVersion`, and `CHANGELOG.md`.
- Bump the Helm chart `version` when chart templates change.

## Git

- Commit messages follow Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`).
- Do not commit unless explicitly asked.
