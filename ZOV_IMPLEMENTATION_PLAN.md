# ZOV (ŽOV) Tracking — Implementation Plan

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

| File | Purpose |
|------|---------|
| `src/bot/__main__.py` | Bot entry, handler registration, monitors startup |
| `src/bot/loader.py` | Config from env vars, lazy init of bot/db/rabbit |
| `src/bot/handlers.py` | All Telegram command/callback handlers, subscribe dialog |
| `src/bot/database.py` | asyncpg CRUD for Users, Applications, Reminders |
| `src/bot/rabbitmq.py` | Publish fetch/refresh, consume updates/expiration/metrics |
| `src/bot/monitor.py` | `ApplicationMonitor` (periodic refresh), `ReminderMonitor` |
| `src/bot/utils.py` | `MVCR_STATUSES` dict, `categorize_application_status()`, `notify_user()` |
| `src/bot/texts/{EN,RU,CZ,UA}/` | i18n JSON files (messages.json, buttons.json, commands.json) |
| `src/fetcher/__main__.py` | Fetcher entry, wires Browser + Messaging + Processor |
| `src/fetcher/browser.py` | Selenium + Firefox, form fill, status extraction |
| `src/fetcher/application_processor.py` | Queue message processing, retries, dedup |
| `src/fetcher/messaging.py` | RabbitMQ client (connect, publish, consume) |
| `src/fetcher/config.py` | Env var config (URL, rabbit, metrics, etc.) |
| `src/tests/test_browser_single_submit.py` | Standalone Selenium test for OAM form |
| `src/tests/test_fetcher_browser_load.py` | Load test using Browser class directly |
| `db-init-scripts/init.sql` | PostgreSQL schema |

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

> **Note**: Multiple online sources (pexpats.com, akswitat.com) claim "4 letters + 9 digits = 13 characters". This is either outdated or refers to a different sub-format. All real ZOV numbers we've observed are 16 characters. The regex should be generous to handle both: `^[A-Z]{4}\d{9,16}$`

**Known embassy city codes** (inferred, not officially documented):
- `ISTA` — Istanbul
- `MOSK` — Moscow
- `KYJV` — Kyiv
- `LVOV` — Lviv
- `BRAT` — Bratislava
- `PEKI` — Beijing
- `HANO` — Hanoi

### Test ZOV Numbers (verified March 2026)

| Number | Response text | Category |
|--------|--------------|----------|
| `ISTA202504220001` | "has been **preliminarily assessed positively**" | approved |
| `ISTA202601150001` | "has been **preliminarily assessed positively**" | approved |
| `ISTA202410300005` | "was **rejected** or the proceedings **have been closed**" | denied |
| `ISTA202601150003` | "is still **being processed**" | in_progress |
| `ISTA202601150010` | "is still **being processed**" | in_progress |
| `ZZZZ000000000000` | "reference number ... **not found**" | not_found |

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

| Status | English (`/en/`) | Czech (default) | Category |
|--------|------|------|----------|
| Approved | "has been **preliminarily assessed positively**" | "bylo **předběžně vyhodnoceno kladně**" | approved |
| Denied | "was **rejected** or the proceedings **have been closed**" | "bylo **nepovoleno**" | denied |
| In progress | "is still **being processed**" | "**zpracovává se**" | in_progress |
| Not found | "reference number ... **not found**" | "**nebylo nalezeno**" | not_found |
| Suspended | *not yet observed* — kept from IPC docs | *not yet observed* | suspended |

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

## Phase 1: Full Integration (after Phase 0 validates)

### Approach: Unified source abstraction

Add an `application_source` discriminator (`"oam"` or `"zov"`) that flows through the entire pipeline. ZOV data is stored in the existing `Applications` table using sentinel values for OAM-specific columns.

For ZOV applications, column mapping:
| Column | ZOV value |
|---|---|
| `application_number` | Full ZOV string, e.g. `ISTA202504220001` |
| `application_suffix` | `"0"` (unused) |
| `application_type` | `"ZOV"` (sentinel, not a real OAM type) |
| `application_year` | `0` (sentinel) |
| `application_source` | `"zov"` |

The existing uniqueness constraint `(user_id, application_number, application_type, application_year)` still works — ZOV numbers are globally unique strings so collisions with OAM numbers are impossible.

### Changes needed per component

1. **DB schema** (`db-init-scripts/init.sql`) — add `application_source` column with default `'oam'`
2. **`utils.py`** — add ZOV status keywords to `MVCR_STATUSES` (confirmed from Phase 0), add `"suspended"` category, add unified identifier formatter alongside existing `generate_oam_full_string()`
3. **`database.py`** — add `application_source` to `insert_application()` and all SELECT queries that feed the monitor/rabbitmq pipeline
4. **`handlers.py`** — ZOV number parsing (regex `^[A-Z]{4}\d{9,16}$`), modified subscribe flow to skip type/year dialogs for ZOV, ZOV-specific confirmation message, `source` field in `create_request()`, ZOV-aware button labels
5. **`application_processor.py`** — `source` flows through automatically via `app_details` dict, no structural changes needed
6. **`rabbitmq.py`** — use unified identifier formatter, extract `source` where needed. Suspended is NOT resolved.
7. **`monitor.py`** — add `source` to all message dicts built from DB query results
8. **i18n** (`src/bot/texts/{lang}/messages.json`) — add ZOV hint to `dialog_app_number`, add `dialog_confirmation_zov` in all 4 languages

---

## Data Flow Summary

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

## Risks & Open Questions

1. **Unknown ZOV statuses**: We've only observed "preliminarily assessed positively". Other statuses ("is being processed", "has been suspended", "was not found", "has been denied/rejected") are inferred from IPC documentation. Phase 0 testing will confirm the exact texts. After deployment, monitor logs for `[UNRECOGNIZED STATUS]` entries.

2. **reCAPTCHA**: The IPC page uses reCAPTCHA v3 (invisible), same as frs.gov.cz. The current approach of ignoring/retrying should work, but the IPC page may have different score thresholds.

3. **OAM fields left empty**: When submitting with only the ZOV field filled, the OAM fields are empty. Phase 0 will confirm this doesn't cause validation errors. The HTML suggests they are independent sections.

4. **IPC page language**: The fetcher currently sets `intl.accept_languages` to `cs-CZ`. ZOV results from the `/en/` URL are in English. **Decision**: Use the English URL for ZOV since embassy applicants are abroad. Status keywords must include English-language strings. Phase 0 testing should also try the Czech URL to compare.

5. **ZOV number format**: Observed 4+12=16 chars, docs say 4+9=13 chars. The regex `^[A-Z]{4}\d{9,16}$` covers both. Phase 0 testing with multiple numbers will help validate. May also want to try lowercase input and verify case normalization.

6. **Backward compatibility**: The `source` field defaults to `"oam"` everywhere, so existing messages in RabbitMQ queues (without `source`) will be treated as OAM. Zero downtime migration.

7. **"preliminarily assessed positively" vs "granted"**: The observed ZOV response says "preliminarily assessed positively" which is subtly different from "granted". It may mean the application is approved in principle but the visa sticker hasn't been issued yet. We map this to `approved` for now, but may need a separate category later.

8. **Finding an "in progress" test number**: We need a ZOV number that returns an in-progress status to verify that keyword. Options: try very recent numbers, ask community members, or wait for a real user to provide one.
