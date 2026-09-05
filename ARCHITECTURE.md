# Architecture

## 1. Overview

MVCR Application Status Notifier is a distributed system that monitors Czech Ministry of Interior
(MVČR) immigration application statuses and notifies users via Telegram.

It supports two application types:

- **OAM** — residency applications filed in the Czech Republic (format: `OAM-12345/TP-2023`)
- **ZOV** — visa applications submitted at Czech embassies abroad (format: `ISTA202504220001`)

The system consists of two independently deployable services communicating asynchronously through RabbitMQ:

```text
                                    +----------------+
                                    |   PostgreSQL   |
                                    |  (persistent   |
                                    |    storage)    |
                                    +-------+--------+
                                            |
+----------+       +--------+       +-------+--------+       +-----------+       +---------------+
|          | <---> |        | <---> |                | <---> |           | <---> |               |
|   User   |       |Telegram|       |    Bot         |       | RabbitMQ  |       |  Fetcher(s)   |
|          | <---> |  API   | <---> |   Service      | <---> |           | <---> |               |
+----------+       +--------+       +----------------+       +-----------+       +-------+-------+
                                                                                         |
                                                                                 +-------+-------+
                                                                                 |    Selenium    |
                                                                                 |   (Firefox)    |
                                                                                 +-------+-------+
                                                                                         |
                                                                                 +-------+-------+
                                                                                 |  MVCR Website  |
                                                                                 | (ipc.gov.cz)   |
                                                                                 +---------------+
```

**Bot** handles user interactions, stores state, schedules periodic checks, and delivers notifications.
**Fetcher** scrapes the MVCR website using Selenium and publishes results back via RabbitMQ.
Multiple fetcher instances can run in parallel, consuming from shared queues.

## 2. Bot Service (`src/bot/`)

Entry point: `src/bot/__main__.py`

The bot runs on `python-telegram-bot` v20.5 with `asyncpg` for PostgreSQL and `aio_pika` for RabbitMQ.
It uses `uvloop` as the event loop policy.

### 2.1 Conversation State Machine

User subscription is a multi-step dialog implemented via `ConversationHandler`:

```text
  /start or /subscribe
         |
         v
  +------+------+
  |    START     |  Show welcome / subscribe button
  +------+------+
         |  [subscribe button]
         v
  +------+------+
  |   SOURCE    |  Choose application type: OAM or ZOV
  +------+------+
         |
    +----+----+
    |         |
    v         v
  [OAM]    [ZOV]
    |         |
    v         v
  +------+------+
  |   NUMBER    |  User types the application number (free text)
  +------+------+
         |
         | (ZOV skips TYPE and YEAR, jumps straight to VALIDATE)
         v
  +------+------+
  |    TYPE     |  Select application type: TP, DP, ZM, etc. (inline buttons)
  +------+------+  Popular types shown first, "Other" expands the rest
         |
         v
  +------+------+
  |    YEAR     |  Select year: current year ± 3 (inline buttons)
  +------+------+
         |
         v
  +------+------+
  |  VALIDATE   |  Confirm or cancel (inline buttons)
  +------+------+
      |       |
  [confirm] [cancel]
      |       |
      v       v
  Subscribe  End
  (publish
   fetch
   request)
```

There are two additional conversation handlers:

- **Broadcast** (`/admin_broadcast`): `BROADCAST_TEXT → BROADCAST_CONFIRM` — admin sends a message to all users
- **Reminder** (`/reminder`): `REMINDER_ADD / REMINDER_DELETE` — schedule daily status checks at a specific time

### 2.2 Command Handlers

| Command            | Description                                                                   |
|--------------------|-------------------------------------------------------------------------------|
| `/start`           | Welcome message, language auto-detection, subscribe button                    |
| `/help`            | Usage instructions                                                            |
| `/subscribe`       | Start the subscription dialog (limit: 5 apps per user)                        |
| `/status`          | Show current status of all subscribed applications                            |
| `/unsubscribe`     | Remove a tracked application (inline buttons to pick which one)               |
| `/force_refresh`   | Trigger an immediate status check (rate-limited: 5/day, unlimited for admins) |
| `/lang`            | Switch language (EN, RU, CZ, UA)                                              |
| `/reminder`        | Add or remove scheduled daily reminders (hour:minute, Prague TZ)              |
| `/admin_stats`     | Admin only — user/subscription counts                                         |
| `/fetcher_stats`   | Admin only — live fetcher metrics                                             |
| `/admin_broadcast` | Admin only — send a message to all users                                      |

Rate limiting uses a sliding window of timestamps per user per command (stored in `context.user_data`).

### 2.3 Application Monitor (Scheduler)

**Startup** (`__main__.py`): Telegram polling, then `asyncio.gather(...)` without `await` to register Rabbit consumers (`consume` returns after `queue.consume`; aio_pika callbacks handle messages). After 15s, `await asyncio.gather(...)` runs the schedulers below until shutdown. Fetcher `__main__.py` uses the same fire-and-forget `gather` for its consumers.

Three background loops run after a 15-second startup delay:

**ApplicationMonitor** (interval: `SCHEDULER_PERIOD`, default 300s):

1. Queries DB for applications needing a status refresh:
   - Normal applications: not refreshed in `REFRESH_PERIOD` (default 3600s)
   - NOT_FOUND applications: not refreshed in `NOT_FOUND_REFRESH_PERIOD` (default 86400s)
   - Resolved applications: excluded entirely
2. Publishes refresh requests to `RefreshStatusQueue`
3. Finds NOT_FOUND applications older than `NOT_FOUND_MAX_DAYS` (default 30) and publishes
   expiration messages to `ExpirationQueue`

**ReminderMonitor** (interval: 60s):

1. Queries DB for reminders matching the current hour:minute in Prague timezone
2. Publishes force-refresh requests to `ApplicationFetchQueue`

**NotificationDispatcher** (interval: `NOTIFY_MONITOR_TICK`, default 60s + on-enqueue wake-up):

1. Atomically claims due rows from the `Notifications` outbox via `FOR UPDATE SKIP LOCKED`,
   joining `Users` and filtering `is_active = TRUE` so blocked users don't generate retries
2. Sends each via Telegram, classifies the outcome, finalizes the row
   (mark delivered / bump attempt with capped exponential backoff / mark user inactive)
3. Purges delivered rows older than `NOTIFY_DELIVERED_RETENTION_DAYS` (default 1) and any
   row older than `NOTIFY_PENDING_MAX_AGE_DAYS` (default 30)

The tick is a backstop. The rabbit consumer calls `dispatcher.wake()` immediately after enqueueing
a notification, so happy-path delivery latency is sub-second instead of bounded by the tick.
Missed wakes (e.g. dispatcher restart with rows already in the table) recover on the next tick.

System-initiated user messages go through the `Notifications` outbox. User-initiated synchronous
replies (`/start`, `/help`, menu navigation, validation errors) stay inline because the user is
actively waiting for the response.

### 2.4 Database Layer

PostgreSQL via `asyncpg` connection pool (min 5, max 20 connections).
`TIMESTAMP` columns are without time zone — Python callers must use naive UTC datetimes.
Auto-runs migrations from `db-migrations/` on startup.

See [Section 7: Database Schema](#7-database-schema) for table definitions.

Key behaviors:

- `update_application_status()` is called only when the polled status genuinely changed;
  it stamps `changed_at` and refreshes the derived (`is_resolved`, `application_state`) fields.
  The "no change" case takes the cheaper `update_last_checked()` path (refreshes `last_updated` only)
- `fetch_applications_needing_update()` uses `last_updated` timestamps to determine refresh eligibility
- `fetch_due_reminders()` matches against current Prague time with minute precision

**Notifications outbox API:**

- `enqueue_notification(chat_id, kind, text, origin_ref)` — single insert, returns row id
- `claim_due_notifications(limit, lock_window_seconds)` — atomic `UPDATE … RETURNING` with
  `FOR UPDATE SKIP LOCKED`, joins `Users` filtered by `is_active = TRUE`, forward-shifts
  `next_attempt_at` by `lock_window_seconds` so a row can't be claimed twice in flight
- `mark_delivered(notification_id, last_error=None)` — sets `delivered_at = NOW()`
- `bump_attempt(notification_id, next_attempt_at, last_error)` — increments attempts,
  reschedules to caller-supplied timestamp (backoff lives in `compute_next_retry_at()`)
- `purge_old_notifications(delivered_retention_days, pending_max_age_days)` — single `DELETE`
  covering both retention classes
- `count_pending_notifications()` — returns pending count and oldest pending age, filtered by
  active users for `/admin_stats`

**User deactivation:**

- `mark_user_inactive(chat_id, reason)` — flipped when Telegram permanently rejects a send
  (`Forbidden: bot was blocked`, `user is deactivated`, `chat not found`); idempotent
- `reactivate_user_if_needed(chat_id)` — flipped back on the user's next interaction
  (called from `_get_user_language()`, which runs on every handler entry); idempotent

### 2.5 RabbitMQ Integration (Bot Side)

The bot both **publishes** and **consumes** messages.

`src/bot/rabbitmq.py` is the RabbitMQ adapter. It owns connections, queue declarations,
message decoding, publishing, deduplication, and the `message.process()` ack/nack boundary.

`src/bot/processor.py` owns the business workflows for messages received from RabbitMQ:

- `process_status_update()` — handles status responses from fetchers
- `process_expiration()` — handles stale NOT_FOUND expiration messages
- `process_fetcher_stats()` — updates the Telegram-facing fetcher stats cache

This keeps queue mechanics separate from business decisions. If a processor raises
unexpectedly, the exception bubbles through `message.process()` and aio-pika keeps the
normal nack/retry behavior.

**Consumes from:**

- `StatusUpdateQueue` (durable) — status results from fetchers
- `ExpirationQueue` (durable) — expiration requests from its own monitor
- `FetcherMetricsQueue` (durable, short-lived messages) — fetcher health/performance metrics

**Publishes to:**

- `ApplicationFetchQueue` — new subscriptions, force refreshes, reminders
- `RefreshStatusQueue` — periodic refresh requests from ApplicationMonitor

**Deduplication:** A TTL cache (keyed by MD5 hash of `request_type + chat_id + number + type + year + last_updated`,
TTL = `REQUEUE_THRESHOLD_SECONDS`, default 3600s) prevents publishing duplicate requests.
When a response arrives, its ID is removed from the cache, allowing re-requests.

**Processor flow:**

```text
RabbitMQ consumer
  |
  |  decode JSON, enter message.process()
  v
Processor
  |
  +-- StatusUpdateQueue -> process_status_update()
  |     |
  |     +-- invalid / mismatched / failed refresh -> drop
  |     |
  |     +-- unchanged normal refresh -------------> update_last_checked()
  |     |
  |     +-- failed initial fetch -----------------> update_application_status(ERROR)
  |     |                                           enqueue failed_fetch notification
  |     |
  |     +-- changed status -----------------------> update_application_status(...)
  |     |                                           enqueue status_change notification
  |     |
  |     +-- unchanged force_refresh --------------> update_last_checked()
  |                                                 enqueue force_refresh_unchanged notification
  |
  +-- ExpirationQueue ----> process_expiration()
  |     |
  |     +-- resolve application ------------------> enqueue expiration notification
  |
  +-- FetcherMetricsQueue -> process_fetcher_stats()
        |
        +-- update in-memory fetcher stats cache for /fetcher_stats

Any enqueued notification wakes NotificationDispatcher, which sends it later from
the Notifications outbox.
```

**Status update processing (`Processor.process_status_update`):**

1. Discard if application number is not found in the status text (misroute guard)
2. If failed refresh → log and drop (prevents mass status overwrites during fetcher issues)
3. If failed initial fetch from a reminder → tolerate silently (user didn't initiate the lookup)
4. If status unchanged and not a force refresh → `update_last_checked` only, no notification
5. If failed initial fetch (non-reminder) → freeze the row (`is_resolved = TRUE`) and
   enqueue a `failed_fetch` notification
6. If status changed → `update_application_status`, enqueue a `status_change` notification
7. If unchanged but force-refreshed → enqueue a `force_refresh_unchanged` notification

In all enqueue cases, the consumer calls `dispatcher.wake()` immediately after the insert so
delivery happens within milliseconds. The dispatcher (§2.3) owns the actual Telegram send,
retries, and dead-user handling. This is the transactional outbox pattern: the consumer's job
ends at "row is durably in the DB" — delivery is asynchronous and survives bot restarts.
The producer stores the final Telegram text in `Notifications.text`. The dispatcher just sends
that text.

**Expiration processing (`Processor.process_expiration`):** marks the row resolved and enqueues
an `expiration` notification on the same outbox.

## 3. Fetcher Service (`src/fetcher/`)

Entry point: `src/fetcher/__main__.py`

The fetcher runs on `uvloop`, connects to RabbitMQ (with optional mutual TLS), and processes
requests by scraping the MVCR website with Selenium.

### 3.1 Selenium Browser

Firefox WebDriver running in a virtual display (Xvfb, 1420x1080):

- **User-Agent:** Randomized via `fake_useragent` (Firefox variants)
- **Language:** Czech (`cs-CZ`) to match MVCR's expected locale
- **Anti-detection:** `navigator.webdriver` property overridden to `undefined`
- **Cookies:** Persisted per user-agent hash to `cookies/` directory (avoids repeated consent dialogs)
- **Human-like behavior:** Random delays between keystrokes (50-150ms) and between actions (0.5-1.5s)
- **Recaptcha:** If detected, waits up to `CAPTCHA_WAIT_SECONDS` (default 120s) for manual solving
- **Session recovery:** Soft/site errors keep the warm browser. Dead Selenium sessions
  (marionette/connection loss) always clear driver refs, then cold-start on the next
  attempt. After `MAX_SESSION_DEAD_FAILURES` consecutive deaths (default 2) the process
  exits so Docker/K8s can restart it

### 3.2 Form Filling

**OAM applications** — fill 4 fields on the form:

1. `proceedings.referenceNumber` — application number
2. `proceedings.additionalSuffix` — suffix (usually "0")
3. `proceedings.category` — React-Select dropdown for type (TP, DP, etc.)
4. Year — React-Select dropdown, scrolling to find the right option

**ZOV applications** — fill 1 field:

1. `visaApplicationNumber` — the full visa number

After filling, the form is submitted via JavaScript click on the submit button.
Status is extracted from the `.alert__content` element. The HTML is cleaned through
BeautifulSoup to strip tags unsupported by Telegram's HTML parser.

Each fetch attempt retries up to 3 times with random backoff (1-20s) at the browser level.

### 3.3 Application Processor

Consumes from two queues:

- `ApplicationFetchQueue` → `fetch_callback()` — new subscriptions, force refreshes
- `RefreshStatusQueue` → `refresh_callback()` — periodic refreshes

**Processing locks:** A dict per request type (`fetch`, `refresh`) tracks currently-processing
applications by `(number, type, year)` key. Duplicate requests for the same app are skipped
(unless it's a retry). Refresh requests also wait if a fetch is in progress for the same app.

**Jitter:** Refresh requests sleep for a random duration (5 to `JITTER_SECONDS`, default 900s)
before processing. This spreads load across time and avoids hammering the MVCR website.

**Request flow:**

```text
  Message arrives from queue
           |
           v
  Already processing? ----yes----> ACK and skip
           |no
           v
  Acquire processing lock
           |
           v
  Refresh? --yes--> Random jitter sleep (5-900s)
           |no              |
           v                v
  browser.fetch(url, app_details)
           |
           +--- status retrieved AND number matches ---> publish to StatusUpdateQueue, ACK
           |
           +--- status empty OR number mismatch -------> requeue with x-retry-count + 1
           |
           +--- retry count > MAX_RETRIES (10) ---------> publish error to StatusUpdateQueue, ACK
           |
           v
  Release processing lock
```

### 3.4 Metrics Collector

Runs a background loop (interval: `METRICS_SEND_INTERVAL`, default 30s):

1. Checks MVCR website connectivity (async HTTP GET, records latency)
2. Computes rates from counters within a sliding window (`METRICS_TTL`, default 1800s):
   - Success / failure / retry counts and rates per `METRICS_RATE` interval (default 600s)
3. Tracks current request states (waiting for jitter vs. actively locked/fetching)
4. Publishes metrics to `FetcherMetricsQueue` (durable queue, 30s message expiration)

Bot displays these via `/fetcher_stats` command from an in-memory TTL cache (300s).

## 4. Message Flow & Queue Architecture

### 4.1 Queues

```text
  Bot                            RabbitMQ                         Fetcher(s)
  ===                            ========                         ==========

  publish -----> [ ApplicationFetchQueue  ] -----> consume (fetch_callback)
                       (durable)

  publish -----> [  RefreshStatusQueue    ] -----> consume (refresh_callback)
                       (durable)

  consume <----- [  StatusUpdateQueue     ] <----- publish
                       (durable)

  consume <----- [   ExpirationQueue      ]
                       (durable)
                 (bot publishes & consumes)

  consume <----- [ FetcherMetricsQueue    ] <----- publish
                     (durable, 30s messages)
```

### 4.2 Message Lifecycle: Subscription to Notification

```text
  1. User sends /subscribe, completes dialog
  2. Bot inserts user + application into PostgreSQL
  3. Bot publishes fetch request to ApplicationFetchQueue
                         |
                         v
  4. Fetcher picks up message, acquires processing lock
  5. Selenium opens MVCR website, fills form, submits
  6. Status HTML extracted, cleaned for Telegram compatibility
  7. Fetcher publishes result to StatusUpdateQueue
                         |
                         v
  8. Bot consumes status update
  9. Bot compares with current_status in DB
 10. If changed (or force_refresh): update DB, enqueue row in Notifications outbox,
     wake the dispatcher
 11. NotificationDispatcher claims the row, sends via Telegram, marks delivered
 12. If resolved: mark is_resolved=TRUE, stop future monitoring
```

### 4.3 Message Lifecycle: Periodic Refresh

```text
  1. ApplicationMonitor wakes up every 300s
  2. Queries DB for applications past their refresh interval
  3. Publishes refresh requests to RefreshStatusQueue (with dedup check)
                         |
                         v
  4. Fetcher picks up message, applies random jitter sleep
  5. Selenium fetches status (same as above)
  6. Publishes result to StatusUpdateQueue
                         |
                         v
  7. Bot processes: if status unchanged, just update last_updated timestamp
  8. If status changed: update DB + application_state, enqueue notification, wake dispatcher
```

## 5. Application Lifecycle & States

### 5.1 State Categories

Status text from the MVCR website is categorized by keyword matching:

| State          | Keywords (Czech/English)                                      | Emoji | Final? |
|----------------|---------------------------------------------------------------|-------|--------|
| `NOT_FOUND`    | "nebylo nalezeno", "not found"                                | ⚪️    | No*    |
| `IN_PROGRESS`  | "zpracovává se", "being processed"                            | 🟡    | No     |
| `PRE_APPROVED` | "předběžně vyhodnoceno kladně", "preliminarily assessed..."   | ⭐️    | Yes    |
| `APPROVED`     | "bylo povoleno", "rizeni-povoleno"                            | 🟢    | Yes    |
| `DENIED`       | "bylo nepovoleno", "zamítlo", "zastavilo", "rejected"         | 🔴    | Yes    |
| `SUSPENDED`    | "přerušeno", "has been suspended"                             | 🟠    | No     |

*NOT_FOUND expires after `NOT_FOUND_MAX_DAYS` (default 30 days).

### 5.2 State Transitions & Refresh Intervals

```text
  New subscription
         |
         v
  +------+-------+                  +----------------+
  |  NOT_FOUND   | --- status  ---> |  IN_PROGRESS   |
  | refresh: 24h |    appears       |  refresh: 1h   |
  +------+-------+                  +-------+--------+
         |                                  |
   30 days pass                     decision made
         |                                  |
         v                       +----------+----------+
  +-----------+                  |          |          |
  | EXPIRED   |           +------+   +------+   +-----+------+
  | resolved  |           |APPROVED| |DENIED |   |SUSPENDED   |
  +-----------+           |resolved| |resolved|  |refresh: 1h |
                          +--------+ +--------+  +------------+
```

When `is_resolved = TRUE`, the application is excluded from all future refresh cycles.

## 6. Error Handling & Retries

### Bot Layer

- **Telegram send (per attempt):** Exponential backoff on `NetworkError`, `TimedOut`,
  `RetryAfter` (up to 5 attempts inside a single send). The send returns a verdict —
  `ok`, `dead_user`, `permanent_other`, or `retryable_gave_up` — which the dispatcher
  uses to decide what to do next (see below)
- **Telegram send (across attempts via outbox):** The dispatcher persists state and retries
  a row using capped exponential backoff (`NOTIFY_RETRY_BASE_INTERVAL` × 2^attempts,
  capped at `NOTIFY_RETRY_MAX_INTERVAL`). There is no per-row attempt cap — the absolute
  age cap (`NOTIFY_PENDING_MAX_AGE_DAYS`, default 30d) bounds retry duration instead
- **Permanent Telegram errors** (`Forbidden: bot was blocked`, `Forbidden: user is deactivated`,
  `Bad Request: chat not found`) flip `Users.is_active = FALSE`. The row stays pending.
  The dispatcher's claim query joins on `is_active = TRUE`, so these rows become invisible
  until the user reactivates by interacting with the bot
- **Notification replay:** Any row can be replayed by resetting `delivered_at = NULL`,
  `attempts = 0`, and `next_attempt_at = NOW()` in `Notifications`
- **PostgreSQL:** Connection retries with exponential backoff (up to 5 attempts, starting at 2s)
- **RabbitMQ:** Connection retries (up to 5 attempts, 5s delay)
- **Bot startup:** Polling start retried up to 15 times on `NetworkError`

### Fetcher Layer

- **Browser-level:** 3 retries per fetch with random backoff (1-20s between attempts)
- **Queue-level:** Failed messages requeued with `x-retry-count` header, up to `MAX_RETRIES` (default 10).
  After exceeding max retries, an error status is published to `StatusUpdateQueue`
- **Recaptcha:** If detected, waits up to 120s before giving up
- **Status validation:** If the returned status text doesn't contain the expected application number,
  the result is treated as a failure and requeued

### Deduplication

- **Bot publish side:** TTL cache prevents publishing the same request within `REQUEUE_THRESHOLD_SECONDS`
- **Bot consume side:** Mismatched application numbers in status responses are silently dropped
- **Fetcher side:** Processing locks prevent concurrent processing of the same application

## 7. Database Schema

```text
  +----------------------+       +------------------------+       +-------------------+
  |        Users         |       |     Applications       |       |     Reminders     |
  +----------------------+       +------------------------+       +-------------------+
  | user_id         (PK) |<--+   | application_id    (PK) |<--+   | reminder_id  (PK) |
  | chat_id     (UNIQUE) |<-+|   | user_id           (FK) |   +-->| application_id(FK)|
  | username             |  ||   | application_number     |   |   |   ON DELETE CASCADE|
  | first_name           |  |+-->| application_suffix     |   |   | user_id       (FK)|
  | last_name            |  |    | application_type       |   |   | reminder_time     |
  | language (def EN)    |  |    | application_year       |   |   | created_at        |
  | is_active (def TRUE) |  |    | current_status (TEXT)  |   |   +-------------------+
  | deactivated_at       |  |    | application_state (50) |   |
  | deactivation_reason  |  |    | created_at             |   |   +-------------------+
  +----------------------+  |    | changed_at             |   |   |   Notifications   |
                            |    | last_updated           |   |   +-------------------+
                            |    | is_resolved (bool)     |   |   | id           (PK) |
                            |    +------------------------+   |   | chat_id (FK)------+
                            |                                 |   | kind              |
                            +---------------------------------+   | text              |
                                                                  | origin_ref        |
                            +-------------------+                 | created_at        |
                            | schema_migrations |                 | delivered_at      |
                            +-------------------+                 | attempts          |
                            | id           (PK) |                 | next_attempt_at   |
                            | filename (UNIQUE) |                 | last_error        |
                            | applied_at        |                 +-------------------+
                            +-------------------+
```

Key fields:

- `current_status` — raw status HTML/text from the MVCR website
- `application_state` — categorized state: NOT_FOUND, IN_PROGRESS, PRE_APPROVED, APPROVED, DENIED, SUSPENDED, UNKNOWN
- `changed_at` — updated only when status text changes
- `last_updated` — updated on every successful check (even if status unchanged)
- `is_resolved` — when TRUE, application is excluded from all refresh cycles
- `reminder_time` — TIME type, matched against current Prague time each minute
- `Users.is_active` — flipped to FALSE when Telegram permanently rejects sends to this chat;
  flipped back to TRUE on next interaction. The dispatcher's claim query filters on this so
  blocked users don't accumulate retries
- `Notifications.kind` — `status_change`, `force_refresh_unchanged`, `failed_fetch`,
  `expiration` (free-form string; the dispatcher is kind-agnostic)
- `Notifications.text` — pre-rendered, ready for Telegram. Rendering happens at enqueue time
  using the user's language, so language switches don't retroactively change pending messages
- `Notifications.origin_ref` — optional caller-provided reference (e.g. `application_id`)
  for forensic correlation
- `Notifications.next_attempt_at` — when the row becomes eligible for delivery; the claim
  query forward-shifts this on claim to act as an in-flight lock

## 8. Internationalization (i18n)

Four languages: English (EN), Russian (RU), Czech (CZ), Ukrainian (UA).

Text files live in `src/bot/texts/{EN,RU,CZ,UA}/`:

- `messages.json` — all bot message templates
- `buttons.json` — inline button labels
- `commands.json` — command descriptions for Telegram menu

Language resolution:

1. Cached in `context.user_data["lang"]` for the session
2. Fetched from `Users.language` column in DB
3. Auto-detected from Telegram user locale via IETF mapping (`en→EN`, `ru→RU`, `cs→CZ`, `uk→UA`)
4. Falls back to `EN`

Users can switch language at any time via `/lang`.

## 9. Deployment

### 9.1 Docker Compose

Two separate compose files for independent scaling:

- `docker-compose-bot.yaml` — PostgreSQL + RabbitMQ + Bot service
- `docker-compose-fetcher.yaml` — Fetcher service (can be scaled with multiple replicas)

### 9.2 Docker Images

- **Bot** (`Dockerfile_bot`): Based on `python:3.11-slim`
- **Fetcher** (`Dockerfile_fetcher`): Based on `ubuntu:jammy` (requires Firefox + geckodriver + Xvfb for Selenium)

### 9.3 Kubernetes

Helm chart manifests live in `deploy/mvcr-application-checker-helm/`: workloads, services,
ConfigMaps, Secrets, ServiceMonitors, and PrometheusRules for both services.
RabbitMQ TLS certificates are mounted from Kubernetes secrets.

### 9.4 RabbitMQ TLS

Mutual TLS is supported (CA cert + client cert + client key).
Configuration in `conf/rabbitmq-server.conf`, self-signed certificates generated via `make ssl`.

### 9.5 Horizontal Scaling

Fetchers scale horizontally — each instance consumes from the same `ApplicationFetchQueue` and
`RefreshStatusQueue` with QoS prefetch (default 10 messages). Adding more fetcher replicas
increases throughput. Each fetcher reports metrics independently via `fetcher_id`.

### 9.6 Key Environment Variables

**Bot:**

| Variable                          | Default | Description                                                                                  |
|-----------------------------------|---------|----------------------------------------------------------------------------------------------|
| `TELEGRAM_BOT_TOKEN`              | —       | Telegram bot API token                                                                       |
| `ADMIN_CHAT_IDS`                  | —       | Comma-separated admin chat IDs                                                               |
| `REFRESH_PERIOD`                  | 3600    | Seconds between status refreshes                                                             |
| `SCHEDULER_PERIOD`                | 300     | Seconds between monitor wake-ups                                                             |
| `NOT_FOUND_REFRESH_PERIOD`        | 86400   | Seconds between NOT_FOUND refreshes                                                          |
| `NOT_FOUND_MAX_DAYS`              | 30      | Days before NOT_FOUND apps expire                                                            |
| `REQUEUE_THRESHOLD_SECONDS`       | 3600    | TTL for deduplication cache                                                                  |
| `NOTIFY_RETRY_BASE_INTERVAL`      | 300     | Base backoff (s) between outbox retries; also acts as the in-flight lock window              |
| `NOTIFY_RETRY_MAX_INTERVAL`       | 3600    | Backoff cap (s)                                                                              |
| `NOTIFY_MONITOR_TICK`             | 60      | Dispatcher poll interval (s); the wake-up signal short-circuits this for happy-path delivery |
| `NOTIFY_DELIVERED_RETENTION_DAYS` | 1       | Days delivered rows survive before purge                                                     |
| `NOTIFY_PENDING_MAX_AGE_DAYS`     | 30      | Hard upper bound on pending row age; absolute retry budget                                   |

**Fetcher:**

| Variable                  | Default    | Description                                |
|---------------------------|------------|--------------------------------------------|
| `URL`                     | ipc.gov.cz | MVCR website URL                           |
| `JITTER_SECONDS`          | 900        | Max random delay before refresh requests   |
| `MAX_RETRIES`             | 10         | Max requeue attempts per failed message    |
| `MAX_SESSION_DEAD_FAILURES` | 2        | Exit fetcher after N consecutive Selenium session deaths |
| `MAX_MESSAGES`            | 10         | RabbitMQ prefetch count per fetcher        |
| `PAGE_LOAD_LIMIT_SECONDS` | 20         | Selenium page load timeout                 |
| `CAPTCHA_WAIT_SECONDS`    | 120        | Max wait for manual captcha solving        |
| `METRICS_SEND_INTERVAL`   | 30         | Seconds between metrics publications       |
