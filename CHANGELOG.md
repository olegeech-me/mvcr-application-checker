# CHANGELOG

## [v2.1.2] - 2026-03-21

### Documentation & Deployment

- Added comprehensive [ARCHITECTURE.md](ARCHITECTURE.md) covering system design, state machines, message flows, database schema, and deployment
- Updated README with Docker image tags, Helm chart install instructions, and Kubernetes deployment guide
- Moved Helm chart from `k8s/` to `deploy/mvcr-application-checker-helm/`
- Removed stale `docs/development.md`
- Chart minor fixes
- Fetcher image fix

## [v2.0.0] - 2026-03-20

### ZOV (visa application) tracking

- Added support for tracking ZOV visa applications (e.g. `ISTA202504220001`) submitted at Czech embassies abroad, in addition to existing OAM applications
- Separate OAM and ZOV subscribe flows in the bot
- Bot: ZOV-specific confirmation messages, button labels, and status notifications (all 4 languages: EN, RU, CZ, UA)
- Database: automatic DB schema migration on startup from the `DB_MIGRATIONS_DIR` folder
- New `pre_approved` status category for "preliminarily assessed positively" responses (treated as resolved/final)
- RabbitMQ messages carry `source` field for correct routing between OAM and ZOV fetchers
- Minor text improvements
- Libs bumps
- Documentation updates

### Developer experience

- Added `Makefile` with targets: `env`, `ssl`, `venv`, `test`, `lint`, `build`, `up`, `down`, `logs`, `clean`
- Integrated `ruff` linter
- 125 tests (up from 29), covering handlers, RabbitMQ, monitor, processor, and i18n
- Test suite split into per-module files (`test_handlers`, `test_rabbitmq`, `test_monitor`, `test_database`, `test_utils`, `test_processor`); manual browser tests moved to `tests/manual/`

## [v1.0.7] - 2026-03-20

- Telegram bot: optional outbound proxy via `HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY` (PTB 20.x `proxy_url` / `get_updates_proxy_url`)

## [v1.0.6] - 2026-03-20

- Telegram bot: optional outbound proxy via `HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY` (PTB 20.x `proxy_url` / `get_updates_proxy_url`)

## [v1.0.5] - 2024-11-23

- Skip running reminders for resolved applications

## [v1.0.4] - 2024-11-17

- Varios bugfixes

## [v1.0.3] - 2024-11-17

- Implemented lazy polling logic for `NOT_FOUND` applications
- Enhanced monitoring and expiration checks for applications not immediately found
- Improved logging and notification systems for application updates

## [v1.0.2] - 2024-11-15

- Added support to keep the application state category stored in the database for more robust state tracking

## [v1.0.1] - 2024-11-12

- Exposed bot and fetcher version details to enhance transparency and tracking across deployments

## [v1.0.0] - 2024-11-09

- Filtered out unsupported HTML tags to ensure proper text formatting
- Updated fetcher to adapt to layout changes on the external website
- Replaced `ALLOWED_YEARS` with a more dynamic `get_allowed_years()` function for improved flexibility

---

### Initial Development Phase (Pre-v1.0.0)

- Added core functionality for status updates and monitoring
- Integrated foundational database management and message queue handling
- Established a basic bot-fetcher workflow to process and track applications
- Added basic testing
- Language translations