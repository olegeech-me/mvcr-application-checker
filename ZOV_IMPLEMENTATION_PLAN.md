# ZOV (ŽOV) Tracking — Implementation Plan

> **⚠️ NOTE FOR AI SESSIONS**: This plan is a high-level roadmap, NOT a final implementation spec. Each stage must be discussed and confirmed with the developer before writing any code. Read the relevant source files, think carefully about implementation details, propose your approach, and wait for approval. Do NOT blindly implement what's written here — the plan may need adjustments based on what you find in the code.

## Context: What This Project Does

This is a **Telegram bot + Selenium fetcher** system that monitors Czech Ministry of Interior (MVČR) residential application statuses and notifies users via Telegram when their status changes.

### Architecture

```
┌──────────┐     RabbitMQ      ┌──────────┐
│ Telegram │ ──────────────►   │ Fetcher  │
│   Bot    │  Fetch/Refresh    │(Selenium)│
│          │  Queues           │          │
│ Postgres │ ◄──────────────   │ Firefox  │
│          │  StatusUpdate     │ headless │
│          │  Queue            │          │
└──────────┘                   └──────────┘
```

- **Bot** (`src/bot/`): Telegram UI, PostgreSQL CRUD, RabbitMQ producer/consumer, periodic monitors
- **Fetcher** (`src/fetcher/`): RabbitMQ consumer, Selenium + Firefox headless, scrapes `frs.gov.cz`
- **Queues**: `ApplicationFetchQueue`, `RefreshStatusQueue`, `StatusUpdateQueue`, `ExpirationQueue`, `FetcherMetricsQueue`
- **DB tables**: `Users`, `Applications`, `Reminders`

### Current OAM tracking flow

1. User sends `/subscribe` → enters OAM number (e.g., `OAM-12345/DP-2023`)
2. Bot parses into 4 components: `number=12345`, `suffix=0`, `type=DP`, `year=2023`
3. Stored in `Applications` table, published to `ApplicationFetchQueue`
4. Fetcher navigates to `frs.gov.cz/informace-o-stavu-rizeni/`, fills 4 form fields (number, suffix, type dropdown, year dropdown), submits
5. Reads `div.alert__content` for the status text
6. Publishes result to `StatusUpdateQueue`
7. Bot consumes, compares with DB, notifies user if changed

### Key source files


| File                                      | Purpose                                                                  |
| ----------------------------------------- | ------------------------------------------------------------------------ |
| `src/bot/__main__.py`                     | Bot entry, handler registration, monitors startup                        |
| `src/bot/loader.py`                       | Config from env vars, lazy init of bot/db/rabbit                         |
| `src/bot/handlers.py`                     | All Telegram command/callback handlers, subscribe dialog                 |
| `src/bot/database.py`                     | asyncpg CRUD for Users, Applications, Reminders                          |
| `src/bot/rabbitmq.py`                     | Publish fetch/refresh, consume updates/expiration/metrics                |
| `src/bot/monitor.py`                      | `ApplicationMonitor` (periodic refresh), `ReminderMonitor`               |
| `src/bot/utils.py`                        | `MVCR_STATUSES` dict, `categorize_application_status()`, `notify_user()` |
| `src/bot/texts/{EN,RU,CZ,UA}/`            | i18n JSON files (messages.json, buttons.json, commands.json)             |
| `src/fetcher/__main__.py`                 | Fetcher entry, wires Browser + Messaging + Processor                     |
| `src/fetcher/browser.py`                  | Selenium + Firefox, form fill, status extraction                         |
| `src/fetcher/application_processor.py`    | Queue message processing, retries, dedup                                 |
| `src/fetcher/messaging.py`                | RabbitMQ client (connect, publish, consume)                              |
| `src/fetcher/config.py`                   | Env var config (URL, rabbit, metrics, etc.)                              |
| `src/tests/test_browser_single_submit.py` | Standalone Selenium test for OAM form                                    |
| `src/tests/test_fetcher_browser_load.py`  | Load test using Browser class directly                                   |
| `db-init-scripts/init.sql`                | PostgreSQL schema                                                        |


---

## What We're Adding: ZOV Tracking

### What is a ZOV?

**ŽOV** = *Žádost o Vízum* — a tracking number assigned to **first-time visa or residency applications submitted at Czech embassies/visa centres** abroad (as opposed to OAM numbers which are for applications filed directly at MVČR offices in Czech Republic).

### ZOV Number Format

**Structure**: 4 uppercase letters (embassy city code) + digits

**Analysis of known numbers:**

```
ISTA202504220001
│   │       │
│   │       └── sequence number (4 digits)
│   └────────── date component YYYYMMDD (20250422 = April 22, 2025)
└────────────── embassy city code (ISTA = Istanbul)

ISTA202503030001 → Istanbul, March 3 2025, sequence 0001
ISTA202410300005 → Istanbul, October 30 2024, sequence 0005
```

**Total length**: 4 letters + 12 digits = **16 characters**.

> **Note**: Multiple online sources (pexpats.com, akswitat.com) claim "4 letters + 9 digits = 13 characters". This is either outdated or refers to a different sub-format. All real ZOV numbers we've observed are 16 characters. The regex should be generous to handle both: `^[A-Z]{4}\d{9,12}$`

**Known embassy city codes** (inferred, not officially documented):

- `ISTA` — Istanbul
- `MOSK` — Moscow
- `KYJV` — Kyiv
- `LVOV` — Lviv
- `BRAT` — Bratislava
- `PEKI` — Beijing
- `HANO` — Hanoi

### Test ZOV Numbers (verified March 2026)


| Number             | Response text                                              | Category    |
| ------------------ | ---------------------------------------------------------- | ----------- |
| `ISTA202504220001` | "has been **preliminarily assessed positively**"           | pre_approved |
| `ISTA202601150001` | "has been **preliminarily assessed positively**"           | pre_approved |
| `ISTA202410300005` | "was **rejected** or the proceedings **have been closed**" | denied      |
| `ISTA202601150003` | "is still **being processed**"                             | in_progress |
| `ISTA202601150010` | "is still **being processed**"                             | in_progress |
| `ZZZZ000000000000` | "reference number ... **not found**"                       | not_found   |


### IPC Page Analysis (from saved HTML in `debug/`)

The status check page at `https://ipc.gov.cz/en/status-of-your-application/` has **one form** with two input sections:

**OAM section** (existing — same as `frs.gov.cz`):

```html
<input name="proceedings.oam" disabled value="OAM">
<input name="proceedings.referenceNumber" placeholder="12345">
<input name="proceedings.additionalSuffix" placeholder="XX">
<input name="proceedings.category" type="hidden"> <!-- react-select dropdown -->
<input name="proceedings.year" type="hidden">     <!-- react-select dropdown -->
```

**ZOV section** (new):

```html
<h2 class="h3 margin-top__20">or visa application number (ŽOV)</h2>
<input name="visaApplicationNumber" placeholder="ABCD123456789..."
       type="text" class="input__control">
<label class="input__label">Visa application number</label>
```

**Submit button** (shared):

```html
<button type="submit" class="button button__primary button--large">validate</button>
```

**Result container** (same for both OAM and ZOV):

```html
<!-- Success example (from verified.html with ISTA202504220001): -->
<div class="alert alert--form-success">
  <div class="alert__content">
    Visa application number<strong> ISTA202504220001 </strong>has been
    <b>preliminarily assessed positively</b>.
    <p></p>
    To book an appointment or for further information, contact
    <a href="...">the Client Centre</a> on +420 974 801 801 ...
    <p></p>
    <b>The status of the procedure is for informational purposes only.</b> ...
  </div>
</div>
```

**Other observations**:

- reCAPTCHA v3 (invisible) present — same as frs.gov.cz
- Page is a WordPress site with React components (react-select for dropdowns)
- The `alert` div can have class `alert--form-success`, and presumably `alert--form-error` / `alert--form-warning` for other outcomes
- Debug pages saved locally: `debug/Status of your Application - ipc.gov.cz.html` (before submit) and `debug/Status of your Application - verified.html` (after submit with ISTA202504220001)

### Known ZOV Status Texts (confirmed via browser testing)


| Status      | English (`/en/`)                                           | Czech (default)                         | Category    |
| ----------- | ---------------------------------------------------------- | --------------------------------------- | ----------- |
| Pre-approved | "has been **preliminarily assessed positively**"           | "bylo **předběžně vyhodnoceno kladně**" | pre_approved |
| Denied      | "was **rejected** or the proceedings **have been closed**" | "bylo **nepovoleno**"                   | denied      |
| In progress | "is still **being processed**"                             | "**zpracovává se**"                     | in_progress |
| Not found   | "reference number ... **not found**"                       | "**nebylo nalezeno**"                   | not_found   |
| Suspended   | *not yet observed* — kept from IPC docs                    | *not yet observed*                      | suspended   |


> **Note**: ZOV "approved" in Czech uses "předběžně vyhodnoceno kladně" (≠ OAM's "povoleno"). Both keywords are in `MVCR_STATUSES`.

---

## Phase 0: Standalone Browser Test — DONE

**Goal**: Before touching any bot code, validate that the Selenium fetcher can submit ZOV numbers on the IPC page and capture results.

**Validated**:

- The form accepts ZOV-only submissions (OAM fields left empty) — works
- Exact status texts captured for approved, denied, in_progress, not_found (see tables above)
- reCAPTCHA did not block us during testing
- The existing `alert__content` CSS selector works for ZOV responses

**Changes**:

1. `browser.py` — refactored form submission into `_dismiss_cookies()`, `_click_submit()`, `_fill_zov_form()`, `_fill_oam_form()`, with routing in `_do_fetch_with_browser()`
2. `config.py` — updated default `URL` to `https://ipc.gov.cz/informace-o-stavu-rizeni/` (Czech IPC page, replaces old `frs.gov.cz`)
3. `test_zov_browser.py` — standalone test covering all 4 status categories
4. `utils.py` — `MVCR_STATUSES` updated with confirmed ZOV keywords (both English and Czech)

---

## Phase 1: Full Integration

### Approach: Unified source abstraction

Add an `application_source` discriminator (`"oam"` or `"zov"`) that flows through the entire pipeline. ZOV data is stored in the existing `Applications` table using sentinel values for OAM-specific columns.

For ZOV applications, column mapping:

| Column               | ZOV value                                |
| -------------------- | ---------------------------------------- |
| `application_number` | Full ZOV string, e.g. `ISTA202504220001` |
| `application_suffix` | `"0"` (unused)                           |
| `application_type`   | `"ZOV"` (sentinel, not a real OAM type)  |
| `application_year`   | `0` (sentinel)                           |
| `application_source` | `"zov"`                                  |

The existing uniqueness constraint `(user_id, application_number, application_type, application_year)` still works — ZOV numbers are globally unique strings so collisions with OAM numbers are impossible.

### Key design decisions

- **`generate_oam_full_string()`** keeps its name. Its body is updated to check `source`/`application_source` — for ZOV it returns the ZOV number directly, for OAM (or absent source) it returns the existing `OAM-N/T-Y` format. Zero call-site changes needed.
- **`pre_approved` status category**: ZOV's "preliminarily assessed positively" is distinct from OAM's "granted/povoleno". It gets its own category `pre_approved` (⭐) in `MVCR_STATUSES`. Unlike `approved`, it is **not resolved** — the bot keeps monitoring for a potential final status change. Keywords moved out of `approved` into `pre_approved`.
- **DB migrations** run automatically on bot startup via a lightweight migration runner in `database.py`. SQL files live in a configurable directory (env var, default `db-migrations/`), tracked by a `schema_migrations` table. `init.sql` stays as-is for fresh Postgres containers via `docker-entrypoint-initdb.d`.
- **Backward compatibility**: `source` defaults to `"oam"` everywhere. Existing RabbitMQ messages without `source` are treated as OAM. Zero downtime migration.

### ZOV data flow (end-to-end)

```
User sends ZOV number (e.g., ISTA202504220001)
  → Bot: parse as ZOV, skip type/year dialogs, confirm
  → Bot: insert into Applications (source="zov", type="ZOV", year=0)
  → Bot: publish to ApplicationFetchQueue with source="zov"
  → Fetcher: consume message, see source="zov"
  → Fetcher: navigate to URL, fill visaApplicationNumber field, submit
  → Fetcher: extract alert__content (same as OAM)
  → Fetcher: publish to StatusUpdateQueue with source="zov"
  → Bot: consume, categorize status, update DB, notify user
```

---

### Stage 1.1: Foundation — DB schema + `utils.py`

**Status**: TODO

**Goal**: Lay the groundwork so that the rest of the codebase can store and identify ZOV applications.

**Scope**:

- `db-init-scripts/init.sql` — add `application_source` column to `CREATE TABLE Applications`, default `'oam'` (for fresh installs)
- Lightweight migration runner in `database.py` — on `connect()`, create `schema_migrations` table if missing, scan a configurable directory (env var, default `db-migrations/`) for `.sql` files, run pending ones in order, record them. Simple, no external dependencies
- `src/bot/loader.py` — add `DB_MIGRATIONS_DIR` env var config
- New directory `db-migrations/` with first migration: `001_add_application_source.sql` (adds column if not exists)
- `src/bot/utils.py` — update `generate_oam_full_string()` body to dispatch on `source`/`application_source`, returning the ZOV number directly for ZOV apps. Add `pre_approved` category to `MVCR_STATUSES` (⭐, not resolved) with keywords "preliminarily assessed positively" / "předběžně vyhodnoceno kladně" moved out of `approved`

**Validates**: Migration runner creates tracking table, applies `001` migration, skips it on re-run. Function returns correct identifiers for both OAM and ZOV dicts. `pre_approved` categorization works.

**Results/Notes**: *(to be filled after completion)*

---

### Stage 1.2: Data layer — `database.py`

**Status**: TODO

**Goal**: Make the database layer accept and return `application_source`.

**Scope**:

- `insert_application()` — accept and persist `application_source`
- SELECT queries that explicitly list columns — add `a.application_source` to: `fetch_applications_needing_update()`, `fetch_applications_to_expire()`, `fetch_due_reminders()`, `fetch_user_reminders()`
- Queries using `SELECT *` (e.g. `fetch_user_subscriptions()`) pick up the new column automatically — no change needed
- Lookup queries (update_status, delete, subscription_exists, etc.) use `(number, type, year)` as keys — the ZOV sentinel values ensure correctness without changes

**Validates**: Can insert a ZOV application with `source="zov"`, query it back, see `application_source` in results.

**Results/Notes**: *(to be filled after completion)*

---

### Stage 1.3: Pipeline plumbing — `monitor.py`, `rabbitmq.py`, `application_processor.py`

**Status**: TODO

**Goal**: Make `source` flow through RabbitMQ messages and ensure logging/error messages use the correct identifier format.

**Scope**:

- `src/bot/monitor.py` — include `source` (from `app["application_source"]`) in all message dicts built by `ApplicationMonitor` and `ReminderMonitor`
- `src/bot/rabbitmq.py` — `on_update_message()` and `on_expiration_message()` use `generate_oam_full_string()` for logging, which now handles ZOV automatically. No structural changes expected, but review for any hardcoded OAM assumptions
- `src/fetcher/application_processor.py` — `source` flows through `app_details` dict automatically. Update `_generate_error_message()` to be source-aware instead of hardcoding OAM format. Review log prefixes

**Validates**: A ZOV message published by the monitor includes `source: "zov"`, flows through fetcher and back, with correct identifiers in all log lines.

**Results/Notes**: *(to be filled after completion)*

---

### Stage 1.4: Subscribe flow — `handlers.py` + i18n

**Status**: TODO

**Goal**: Users can subscribe to ZOV applications through the Telegram bot.

**Scope**:

- `src/bot/handlers.py`:
  - Add ZOV number parser (regex `^[A-Z]{4}\d{9,12}$`, case-normalized)
  - Modify `application_dialog_number()` — try ZOV parse before falling back to partial OAM parse. ZOV match skips type/year dialogs, jumps straight to confirmation
  - Modify `subscribe_command()` — handle ZOV in `context.args` path too
  - Modify confirmation dialog — ZOV-specific message (no OAM formatting)
  - Modify `create_request()` — include `source` field
  - Modify `create_subscription()` — pass `source` to `db.insert_application()`
  - Modify `_generate_buttons_from_subscriptions()` — use `generate_oam_full_string()` (now source-aware) for button labels instead of hardcoded OAM format
  - Modify `clean_sub_context()` — include `application_source` in cleanup keys
- `src/bot/texts/{EN,RU,CZ,UA}/messages.json`:
  - Update `dialog_app_number` — mention ZOV as alternative input
  - Add `dialog_confirmation_zov` — confirmation message showing ZOV number

**Validates**: Full subscribe flow works for ZOV number in Telegram — parse, confirm, insert into DB, publish to queue.

**Results/Notes**: *(to be filled after completion)*

---

### Stage 1.5: Tests

**Status**: TODO

**Goal**: Verify the integration with automated tests.

**Scope**:

- `src/tests/test_bot.py` — add ZOV test cases for: number parsing (valid/invalid), `create_request()` with source, `generate_oam_full_string()` with ZOV dicts
- Review existing OAM tests still pass — the `generate_oam_full_string()` body change must not break them

**Validates**: `tox` passes with both old OAM and new ZOV tests.

**Results/Notes**: *(to be filled after completion)*

---

## Dev Notes

- **Local testing**: No `tox` installed globally on macOS. Use the project `.venv` to run tests:
  ```
  PYTHONPATH=src .venv/bin/python -m pytest -vvv src/tests/test_bot.py
  ```

---

## Risks & Open Questions

1. **Backward compatibility**: The `source` field defaults to `"oam"` everywhere, so existing messages in RabbitMQ queues (without `source`) will be treated as OAM. Zero downtime migration.
2. **reCAPTCHA**: The IPC page uses reCAPTCHA v3 (invisible), same as frs.gov.cz. The current approach of ignoring/retrying should work, but the IPC page may have different score thresholds.
3. **ZOV number format**: Observed 4+12=16 chars, docs say 4+9=13 chars. The regex `^[A-Z]{4}\d{9,12}$` covers both. Phase 0 testing validated with multiple numbers.
4. **`pre_approved` i18n**: Stage 1.4 needs a `pre_approved` notification message in all 4 languages alongside the ZOV-specific messages.

