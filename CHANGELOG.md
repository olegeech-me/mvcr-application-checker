# CHANGELOG

## [v2.4.3] - 2026-09-06

### Fetcher

- Recover from dead Selenium sessions without closing the browser on soft/site errors; exit after consecutive session deaths so the orchestrator can restart

## [v2.4.2] - 2026-06-27

### Bot monitoring

- Fix `MvcrBotTelegramStale` false alerts: inbound activity handler now runs in group -1 before command handlers
- Raise stale threshold to 4h / 30m `for`; clearer alert summary and description

## [v2.4.1] - 2026-06-26

### Notification retry fix

- Fixed `compute_next_retry_at()` to return naive UTC timestamps compatible with the `Notifications.next_attempt_at` `TIMESTAMP` column; tz-aware values caused `bump_attempt` to fail and stuck notifications to retry every lock window instead of backing off
- Record `db_error` in Prometheus when `bump_attempt` fails

### Bot Telegram observability

- Record runtime `telegram/timeout` and `telegram/network` errors from `notify_user` retries and the PTB error handler (startup was the only path counted before)
- Added `mvcr_bot_telegram_last_ok_timestamp_seconds` gauge, updated on successful sends and inbound updates
- Added `MvcrBotTelegramStale` PrometheusRule alert when Telegram activity goes silent for over an hour
- Lowered `MvcrBotErrorBurst` and `MvcrTelegramDeliveryFailures` thresholds and `for` durations so scattered outages page sooner

### Tests

- Added contract tests locking the monitor-to-database timestamp shape for notification backoff

## [v2.4.0] - 2026-06-07

### Bot architecture

- Split bot RabbitMQ queue mechanics from business workflows: `rabbitmq.py` is now the adapter, `processor.py` owns status update, expiration, and fetcher-stats processing

### Fetcher guardrails

- Limit fetched status text to 20000 characters so accidental oversized responses are retried and reported instead of being stored

### Monitoring

- Added Prometheus `/metrics` endpoints for bot and fetcher services
- Added Prometheus metrics covering bot and fetcher health, queue processing, notification delivery, MVCR target availability, and fetcher processing state
- Added Helm metrics services, ServiceMonitors, and PrometheusRule alerts for high-signal app failures

## [v2.3.2] - 2026-06-06

### Database

- Store application status text without a 1000-character limit so long resolved/pre-approved MVCR responses can be saved and notified

## [v2.3.1] - 2026-05-24

### Fetcher stability

- Fixed Selenium cookie loading so saved cookies are added only after navigating to the target domain, preventing `Document is cookie-averse` failures
- Made cookie loading best-effort: malformed, stale, or Selenium-rejected cookies are skipped instead of failing the fetch
- Hardened cookie consent dismissal with a scroll/JavaScript click fallback for cases where the button cannot be scrolled into view

## [v2.3.0] - 2026-04-22

### Reliable user notifications

- Transactional outbox: every system-initiated user-facing message (status change, force-refresh confirmation, failed-fetch error, NOT_FOUND expiration) is now persisted in a `Notifications` table before delivery, so nothing is lost on bot restart or transient Telegram API failure
- New `NotificationDispatcher` drains the outbox with capped exponential backoff on retryable errors and an `asyncio.Event` wake-up signal from producers for sub-second happy-path latency
- Auto-deactivate users whose Telegram chat is permanently unreachable (`Forbidden: bot was blocked`, `user is deactivated`, `chat not found`); auto-reactivate on their next interaction with the bot
- Inlined cleanup keeps the outbox bounded: delivered rows purged after 1 day, anything older than 30 days dropped unconditionally
- `/admin_stats` shows pending-notification backlog (count + oldest age) and inactive-user count
- New env knobs: `NOTIFY_RETRY_BASE_INTERVAL`, `NOTIFY_RETRY_MAX_INTERVAL`, `NOTIFY_MONITOR_TICK`, `NOTIFY_DELIVERED_RETENTION_DAYS`, `NOTIFY_PENDING_MAX_AGE_DAYS`
- 212 tests (up from 161)

## [v2.2.0] - 2026-03-27

- Human-readable user identities in log output instead of bare chat IDs
- Two-tier log format: compact labels for routine background ops, full details for important events
- Lightweight profile sync-on-touch to keep DB data fresh
- Added tests (161 total)

## [v2.1.5] - 2026-03-21

- Added GitHub repository link to startup/help messages (all 4 languages)
- Update admin broadcast function
- Add rabbit metrics support
- Update metrics queue config in fetcher

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
