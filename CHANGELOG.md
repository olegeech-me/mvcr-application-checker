# CHANGELOG

## [v2.0.0] - 2026-03-20

### ZOV (visa application) tracking

- Added support for tracking ZOV visa applications (e.g. `ISTA202504220001`) submitted at Czech embassies abroad, in addition to existing OAM applications
- Fetcher: both OAM and ZOV applications are now tracked via `ipc.gov.cz` (replaces `frs.gov.cz`)
- Bot: ZOV number parser accepts any 4-letter embassy code (ISTA, MOSK, KYJV, etc.) followed by 9-12 digits
- Bot: ZOV subscribe flow skips type/year dialogs — users enter the full number and confirm
- Bot: ZOV-specific confirmation messages, button labels, and status notifications (all 4 languages: EN, RU, CZ, UA)
- New `pre_approved` status category for "preliminarily assessed positively" responses (not treated as resolved — bot keeps monitoring)
- Database: `application_source` column (`oam`/`zov`) with automatic migration on startup
- RabbitMQ messages carry `source` field for correct routing between OAM and ZOV fetchers
- Full backward compatibility — existing OAM subscriptions and messages are unaffected

### Developer experience

- Added `Makefile` with targets: `env`, `ssl`, `venv`, `test`, `lint`, `build`, `up`, `down`, `logs`, `clean`
- Integrated `ruff` linter
- 127 tests (up from 29), covering handlers, RabbitMQ, monitor, processor, and i18n

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