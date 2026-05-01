# Notification Reliability — Implementation Plan

This document describes the implementation of durable system-initiated notifications
for the bot via a **transactional outbox**. It supersedes an earlier per-row design
that bolted durability state onto the `Applications` table for one specific message
type. The outbox approach makes every system-initiated user-facing message durable
through a single generic mechanism.

This file is transient — it lives in `feat/notification-monitor` for review and
will be deleted at merge time.

---

## 1. Problem statement

Two defects in the original bot codebase:

1. **Lost status-change notifications.** `on_update_message` writes the new status
   to the database *before* attempting to deliver the Telegram message. If
   `notify_user`'s 5-attempt exponential backoff exhausts, the change is silently
   dropped. The next refresh sees `has_changed=False` (DB already advanced), and for
   final states (`APPROVED`/`DENIED`/`PRE_APPROVED`) the application is also marked
   `is_resolved=TRUE` and excluded from all future refresh cycles — so the notification
   is never re-attempted.

2. **Death loops on unreachable users.** `Forbidden` (bot blocked, user deactivated)
   and `BadRequest("chat not found")` are not detected — they fall into the bare
   `except Exception` branch in `notify_user`, get logged once, and the user's
   applications stay in the refresh cycle. Every status change keeps invoking the
   same dead chat. Same problem in `_broadcast_to_users` (handlers.py) and
   `on_expiration_message` (rabbitmq.py).

A broader, related concern surfaced during design:

3. **All other system-initiated messages are best-effort too.** Reminders, expiration
   notifications, failed-fetch error messages, and force-refresh "no change"
   confirmations all share the same fragility. A per-message-type durability bolt-on
   would pay the same cost N times.

---

## 2. Approach: transactional outbox

One generic table (`Notifications`) into which every system-initiated user-facing
message is enqueued as a fully rendered text row. One generic worker
(`NotificationDispatcher`) drains it. Producers fire-and-forget; the dispatcher owns
delivery, retries, and dead-user handling. User-initiated synchronous replies
(`/start`, `/help`, menu navigation) stay inline — those are user-blocked and
"delivered" means "the user saw it on their screen."

```
┌──────────────────────────┐       ┌─────────────────────┐       ┌────────────────┐
│  Producers               │       │  Notifications      │       │  Dispatcher    │
│  (consumers, monitors)   │ ───►  │  table              │ ───►  │  (1 worker)    │ ───► Telegram
│  enqueue text + kind     │       │  pending → due      │       │  generic, kind │
└──────────────────────────┘       └─────────────────────┘       │  -agnostic     │
       │                                     ▲                   └────────────────┘
       └─ status_change                      │                          │
       └─ failed_fetch                       │                          ├─ ok               → mark_delivered
       └─ expiration                         │                          ├─ retryable_gave_up → bump_attempt
       └─ force_refresh_unchanged            │                          ├─ dead_user        → mark_user_inactive (row stays)
                                             │                          └─ permanent_other  → mark_delivered + last_error
                                             │                                       │
                                             └───────────────────────────────────────┘
                                                  reactivation re-exposes pending rows
```

### 2.1 Why pre-rendered text (not deferred render)

Producers build the full text at enqueue time and store it in `Notifications.text`.
The dispatcher sends `row["text"]` verbatim — no kind-aware rendering, no renderer
registry, no JSONB payload schema.

Considered and rejected: a `(kind, payload JSONB)` schema rendered at dispatch time.
That gains "user changed language between enqueue and delivery picks up new lang"
and "template change picks up retroactively for in-flight rows" — both vanishingly
rare given the dispatcher tick is seconds. It costs a renderer registry, a kind
→ renderer dispatch in the hot path, an unknown-kind failure mode, and a JSONB
payload contract per kind. Pre-render is operationally simpler, the row is
human-readable, and "snapshot of the event at the time it happened" is the right
semantic for these messages.

### 2.2 Why not deduplicate / supersede in v1

The dispatcher's atomic claim already prevents *double delivery* of any single row.
"Dedup" would mean collapsing multiple pending rows for the same `(kind, origin_ref)`
target — e.g., if MVCR flaps a status three times while TG is down, the user gets
three back-to-back notifications. Without supersedence, the user sees the true
event history. Adding supersedence later is a single `WHERE NOT EXISTS` clause at
enqueue time. Skip in v1.

### 2.3 Reactivation flow

The dispatcher's claim query joins `Users` and filters `is_active = TRUE`.
A row owed to a deactivated user is invisible to the dispatcher. When the user
re-engages, `_get_user_language` flips `is_active = TRUE` (cheap UPDATE on every
input), and the dispatcher's next tick exposes the backlog. No special-case code.

---

## 3. Schema changes (`db-init-scripts/init.sql`)

**Drop** (these were added by an earlier design iteration on the feature branch
and will not ship):

```sql
ALTER TABLE Applications
    DROP COLUMN last_notified_status,
    DROP COLUMN notify_attempts,
    DROP COLUMN next_notify_at;
DROP INDEX idx_applications_pending_notify;
```

**Keep on Users** (orthogonal, dead-user handling):

```sql
-- is_active, deactivated_at, deactivation_reason + idx_users_is_active stay
```

**Add** the outbox table:

```sql
CREATE TABLE IF NOT EXISTS Notifications (
    id              BIGSERIAL PRIMARY KEY,
    chat_id         BIGINT      NOT NULL REFERENCES Users(chat_id),
    kind            VARCHAR(64) NOT NULL,           -- metadata only; dispatcher ignores it
    text            TEXT        NOT NULL,           -- pre-rendered, ready for Telegram
    origin_ref      BIGINT      NULL,               -- application_id / reminder_id, for future dedupe
    created_at      TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    delivered_at    TIMESTAMP   NULL,               -- NULL = pending
    attempts        INT         NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_error      TEXT        NULL
);
```

No secondary index. The cleanup loop (§4.2) caps the table at single-digit
thousands of rows in the worst case, where a seq scan in PG is sub-millisecond.
We can add a partial index on `(next_attempt_at) WHERE delivered_at IS NULL`
later if `EXPLAIN` ever shows the claim query getting expensive.

---

## 4. Bot-side code changes

### 4.1 `src/bot/database.py`

**Drop** (replaced by the outbox machinery):

- `mark_notified`
- `bump_notify_retry`
- `fetch_pending_notifications`
- `claim_pending_for_delivery`
- `count_pending_notifications` (re-added with the same name, different query)
- `record_status_changed`, `record_status_unchanged`, `_run_status_update`

**Simplify** `update_application_status` back to a single non-modal method (no
`has_changed` boolean, no conditional SQL). Always called when the status genuinely
changed; stamps `changed_at`, refreshes the four status fields. Returns
`application_id` (used as `origin_ref` when enqueueing the notification).

**Add** the four outbox methods:

- `enqueue_notification(chat_id, kind, text, origin_ref=None)` — single
  `INSERT … RETURNING id`.
- `claim_due_notifications(limit, lock_window_seconds)` — single CTE update with
  `FOR UPDATE SKIP LOCKED`. Selects up to `limit` rows where
  `delivered_at IS NULL AND next_attempt_at <= NOW()` joined to active users,
  forward-shifts their `next_attempt_at` by `lock_window_seconds` (the in-flight
  lock), and returns them. Multi-worker safe even though we run a single dispatcher.
- `mark_delivered(notification_id, last_error=None)` — sets `delivered_at = NOW()`.
  Used for both `ok` and `permanent_other` verdicts.
- `bump_attempt(notification_id, next_attempt_at, last_error)` — increments
  `attempts`, sets the caller-supplied `next_attempt_at` (backoff math lives in
  the dispatcher, see §4.2). Trivial SQL.
- `purge_old_notifications(delivered_retention_days, pending_max_age_days)` —
  single `DELETE` that drops (a) delivered rows older than the retention window
  and (b) any row past the absolute max age. Backs the cleanup loop in §4.5.

**Add** new `count_pending_notifications()`: returns
`(pending_count, oldest_pending_age_seconds)` from the `Notifications` table,
filtered by active users. Same name as before, new query target.

**Keep unchanged:** `update_last_checked`, `mark_user_inactive`,
`reactivate_user_if_needed`, `count_inactive_users`, all `is_active = TRUE`
filters added to the fetch queries.

### 4.2 `src/bot/monitor.py`

**Add** `compute_next_retry_at(current_attempts, base_interval, max_interval, now=None)`:
capped exponential backoff `NOW + min(base * 2^current_attempts, max)`. Pure
function, deterministic (`now` injectable), parametrized-tested. Lives next to
its only caller.

**Replace** `NotificationMonitor` with `NotificationDispatcher`. ~25 lines of
real logic, kind-agnostic:

```python
class NotificationDispatcher:
    async def deliver_pending(self):
        rows = await self.db.claim_due_notifications(
            limit=100, lock_window_seconds=NOTIFY_RETRY_BASE_INTERVAL,
        )
        for row in rows:
            verdict = await notify_user(self.bot, row["chat_id"], row["text"])
            await self._finalize(row, verdict)

    async def _finalize(self, row, verdict):
        if verdict == "ok":
            await self.db.mark_delivered(row["id"])
        elif verdict == "dead_user":
            await self.db.mark_user_inactive(row["chat_id"], "send_message returned dead_user")
            # row stays pending; reactivation re-exposes it
        elif verdict == "retryable_gave_up":
            next_at = compute_next_retry_at(row["attempts"],
                                            NOTIFY_RETRY_BASE_INTERVAL,
                                            NOTIFY_RETRY_MAX_INTERVAL)
            await self.db.bump_attempt(row["id"], next_at, last_error="retryable_gave_up")
        elif verdict == "permanent_other":
            await self.db.mark_delivered(row["id"], last_error="permanent_other")
        else:
            logger.error(f"Unknown verdict from notify_user: {verdict!r}")
```

**Wake-up signal.** The dispatcher waits on `shutdown_event OR wakeup_event OR
NOTIFY_MONITOR_TICK`. Outbox writers call `dispatcher.wake()` immediately after
`enqueue_notification` so happy-path delivery latency is sub-second instead of
bounded by the tick. The tick stays as a backstop: if a wake-up was missed
(e.g. dispatcher restart with rows already in the table), recovery still
happens within `NOTIFY_MONITOR_TICK`. The wake-up is best-effort — losing it
costs at most one tick of latency.

**Cleanup, inlined.** Same loop body, right after `deliver_pending`, the
dispatcher calls `db.purge_old_notifications(NOTIFY_DELIVERED_RETENTION_DAYS,
NOTIFY_PENDING_MAX_AGE_DAYS)`. Two effects:

- Delivered rows survive `NOTIFY_DELIVERED_RETENTION_DAYS` (default 1 day) —
  enough forensic window to correlate with logs / Telegram complaints, then gone.
- Any row past `NOTIFY_PENDING_MAX_AGE_DAYS` (default 30 days) is dropped
  unconditionally. This is the hard upper bound: it caps table size, and it
  also caps how long the dispatcher will keep retrying a stuck row whose
  content (e.g. a status notification from a month ago) is no longer relevant.
  Cheaper and more honest than a per-row `max_attempts` counter, which would
  otherwise have to be tuned against the backoff curve to mean anything.

Single `DELETE` per tick, fast on small tables, no separate scheduler. If we
ever want to optimize, we can add an index on `(delivered_at)` or skip the
purge most ticks — neither is needed at our scale.

**Leave `ApplicationMonitor` and `ReminderMonitor` untouched.** `ReminderMonitor`
publishes a fetch request to RabbitMQ (it doesn't deliver to the user directly);
the consumer at the other end of that fetch is what enqueues a notification —
so reminders become durable for free without touching `ReminderMonitor`.

### 4.3 `src/bot/rabbitmq.py`

**`on_update_message` flow becomes:**

```python
if not has_changed and not force_refresh:
    await db.update_last_checked(...)        # case A: routine poll, nothing new
    return

# from here: has_changed OR force_refresh

if failed and request_type == "fetch":
    if is_reminder:
        logger.error(...)
        return                                # silent on reminder fetch failures (existing)
    # initial-fetch failure: freeze the row, enqueue an error message
    is_resolved = True
    application_id = await db.update_application_status(..., is_resolved=True, ...)
    text = self._generate_error_message(msg_data, lang)
    await db.enqueue_notification(chat_id, "failed_fetch", text, origin_ref=application_id)
    return

if has_changed:
    application_id = await db.update_application_status(...)   # case C
    text = render_status_notification_text(lang, received_status)
    await db.enqueue_notification(chat_id, "status_change", text, origin_ref=application_id)
else:
    # case B: force_refresh, status came back identical
    await db.update_last_checked(...)
    text = message_texts[lang]["application_unchanged"].format(...)   # or similar
    await db.enqueue_notification(chat_id, "force_refresh_unchanged", text, origin_ref=application_id_lookup)
```

Notes:
- `update_application_status` is the simple one-shot version (no boolean, no lock).
- `render_status_notification_text` migrates here from the soon-to-be-deleted
  `notifier.py`.
- The `if not has_changed` "stale derivation" rewrite of `is_resolved` /
  `application_state` is intentionally dropped — see §6 for rationale.

**`on_expiration_message`** stops calling `notify_user` directly and enqueues:

```python
if await db.resolve_application(application_id):
    lang = await db.fetch_user_language(chat_id)
    text = message_texts[lang]["not_found_expired"].format(app_string=oam_full_string)
    await db.enqueue_notification(chat_id, "expiration", text, origin_ref=application_id)
```

Drops the inline `notify_user` + `mark_user_inactive` block. The dispatcher
handles dead-user flagging uniformly.

### 4.4 Delete `src/bot/notifier.py`

`deliver_status_notification` is gone (the dispatcher does this generically). The
two remaining helpers move:

- `render_status_notification_text` → `src/bot/rabbitmq.py` (where its only
  producer caller lives).
- `compute_next_retry_at` → `src/bot/monitor.py` (next to the dispatcher).

Deleting the file removes one indirection layer and matches the user-facing
simplification: there is no longer a "deliver" abstraction worth its own module.

### 4.5 `src/bot/handlers.py`

- **`_get_user_language` reactivation:** unchanged. Stays.
- **`_broadcast_to_users` verdict handling:** unchanged. Stays. (Broadcasts could
  also go through the outbox eventually, but they're admin-initiated; out of scope.)
- **`/admin_stats` pending-notifications block:** queries `Notifications` instead
  of `Applications`. Same `(count, oldest_age_seconds)` shape, same human-readable
  rendering, no other changes.

### 4.6 `src/bot/__main__.py`

Rename `NotificationMonitor` → `NotificationDispatcher` in the wiring. Same
lifecycle (start, stop on shutdown). One line changes.

### 4.7 `src/bot/config.py`

The three existing env vars (`NOTIFY_RETRY_BASE_INTERVAL`,
`NOTIFY_RETRY_MAX_INTERVAL`, `NOTIFY_MONITOR_TICK`) carry over unchanged. Two
new ones for the cleanup step (§4.2): `NOTIFY_DELIVERED_RETENTION_DAYS`
(default `1`) and `NOTIFY_PENDING_MAX_AGE_DAYS` (default `30`).

### 4.8 `src/bot/utils.py`

Untouched. `classify_send_error` and `notify_user` (with verdict return) stay
as-is — they are exactly what the generic dispatcher needs.

### 4.9 `src/bot/loader.py`

Untouched.

---

## 5. Test surface

### 5.1 Delete

- `src/tests/test_notifier.py` — entire file (deliver_status_notification gone).
- `test_database.py`: tests for `record_status_changed`, `record_status_unchanged`,
  `mark_notified`, `bump_notify_retry`, `fetch_pending_notifications`,
  `claim_pending_for_delivery`, the old `count_pending_notifications`.
- `test_monitor.py`: all `NotificationMonitor.deliver_pending` tests.
- `test_rabbitmq.py`: the dispatcher-mocking machinery
  (`test_rabbit_on_update_passes_application_id_to_dispatcher`,
  `test_rabbit_on_update_skips_dispatcher_when_update_returns_none`, the
  failed-fetch dead-user test that asserted `mark_notified`).

### 5.2 Add

- **`test_database.py`** — one test per outbox method, each exercising its single
  static SQL:
  - `enqueue_notification` returns the new id, the `INSERT` mentions the right
    columns.
  - `claim_due_notifications` SQL uses `FOR UPDATE SKIP LOCKED`, joins `Users`
    on `is_active = TRUE`, forward-shifts `next_attempt_at`.
  - `mark_delivered` SQL sets `delivered_at = NOW()`, accepts `last_error`.
  - `bump_attempt` SQL increments `attempts`, persists the caller-supplied
    `next_attempt_at`.
  - `count_pending_notifications` queries the new table, filters active users.
  - `purge_old_notifications` issues a single `DELETE` covering both the
    delivered-retention and the pending-max-age conditions.

- **`test_monitor.py`** — `NotificationDispatcher.deliver_pending`, four verdict
  outcomes against a single generic row (no kind-specific assertions). Plus the
  parametrized `compute_next_retry_at` policy test (moved from `test_notifier.py`),
  the wake() / shutdown_event waiter tests, and a one-iteration smoke test
  asserting `purge_old_notifications` is invoked each tick after delivery.

- **`test_rabbitmq.py`** — for each producer call site
  (`on_update_message` change-path, force-refresh-unchanged path, failed-fetch
  initial-fetch path; `on_expiration_message`): assert the consumer calls
  `enqueue_notification` with the right `(kind, text, origin_ref)`. No dispatcher
  mocking — dispatcher tests cover that side.

### 5.3 Keep unchanged

- `test_utils.py` — `classify_send_error` / `notify_user` verdict tests.
- `test_handlers.py` — `_get_user_language` reactivation, `_broadcast_to_users`
  verdict handling, `/admin_stats` shape.

Net result: roughly the same test count as today, but **per-test complexity drops
materially** because no test has to mock a multi-step dispatch dance anymore.

---

## 6. What we drop and why it's fine

### 6.1 Per-poll defensive rewrite of `is_resolved` / `application_state` for the unchanged path

The earlier `record_status_unchanged` rewrote these derived fields on every
unchanged poll, intended as auto-heal after categorization-logic changes. Analysis:

- `is_resolved` and `application_state` are pure functions of `current_status`.
  If `current_status` is unchanged (which is how we know `has_changed=False`),
  the derived values can only change via a code change, not via a fresh poll.
- `fetch_applications_needing_update` filters `is_resolved = FALSE` — so any
  "auto-heal" only ever applies to currently-polled rows, not to the resolved
  rows that would also be affected by a categorization change.
- `application_state` is only filtered by `= 'NOT_FOUND'` in queries. Reshuffling
  the `NOT_FOUND` membership in `MVCR_STATUSES` is the only categorization change
  that would observably affect anything. That has happened approximately never
  in project history.

If a categorization change ever does need to back-propagate to existing rows, a
one-line `UPDATE Applications SET application_state = …` at deploy time fixes
*all* affected rows including resolved ones — a strict improvement over the
"wait for the next poll, hope they're still being polled" auto-heal.

### 6.2 The consumer↔monitor race

The earlier design needed `next_notify_at` as both a backoff timer AND an
in-flight lock to prevent the consumer and monitor from racing for the same
`Applications` row. With the outbox there is no race: the consumer enqueues a
row, the dispatcher claims it. Single owner per row at all times.

### 6.3 The `__RENOTIFY__` sentinel

Earlier design used `current_status = '__RENOTIFY__'` to manually re-trigger a
dropped notification (set the column, the next monitor tick picks it up). With
the outbox, replay is trivial:

```sql
UPDATE Notifications
SET delivered_at = NULL, attempts = 0, next_attempt_at = NOW()
WHERE id IN (...);
```

No sentinel, no risk of accidentally sending the literal sentinel string.

---

## 7. What we ALSO get for free

Beyond what the original two defects required:

- **Reminders are durable.** The reminder→fetch→consumer→outbox path inherits the
  dispatcher's retry semantics. TG outage during a reminder window? The
  resulting status-change or force-refresh notification stays pending until TG
  recovers.
- **Expirations are durable.** Same.
- **Failed-fetch error messages are durable.** Earlier design treated these as
  best-effort; with the outbox they're free.
- **Force-refresh "unchanged" confirmations are durable.** Same.
- **One mental model** for "how messages reach users" — forever.
- **One-line replay** for any incident (see §6.3).
- **Dedupe / supersedence is one query away** (`WHERE NOT EXISTS … kind +
  origin_ref + delivered_at IS NULL`) if it ever bites us.
- **Foundation for future work**: scheduled messages, per-kind retry policy,
  message-level metrics, kinds we don't have yet.

---

## 8. Configuration (env vars)

```
# bot.sample.env
NOTIFY_RETRY_BASE_INTERVAL=300        # backoff base AND in-flight lock window (seconds)
NOTIFY_RETRY_MAX_INTERVAL=3600        # backoff cap (seconds)
NOTIFY_MONITOR_TICK=60                # dispatcher polling interval (seconds)
NOTIFY_DELIVERED_RETENTION_DAYS=1     # how long delivered rows survive before purge
NOTIFY_PENDING_MAX_AGE_DAYS=30        # absolute hard cap, drops anything older
```

The first three are preserved from the earlier iteration to avoid churn; they
now apply to `NotificationDispatcher` rather than `NotificationMonitor`. The
two retention knobs back the cleanup step (§4.2). Helm chart values mirror the
env vars, no schema change there.

---

## 9. Phases of execution

Implementation is a single PR off `feat/notification-monitor`. Phases are
internal sequencing only — no intermediate commits.

1. **Schema** — `db-init-scripts/init.sql`: drop the three Applications columns +
   index, add the `Notifications` table + index.
2. **Database methods** — drop the five outbox-on-Applications methods, simplify
   `update_application_status` to its non-modal form, add the four outbox methods.
3. **Dispatcher** — replace `NotificationMonitor` with `NotificationDispatcher`
   in `src/bot/monitor.py`. Move `compute_next_retry_at` here.
4. **Producer rewiring** — `rabbitmq.py` `on_update_message` and
   `on_expiration_message`. Move `render_status_notification_text` here from
   `notifier.py`.
5. **Cleanup** — delete `src/bot/notifier.py`, update imports in `__main__.py`
   and `handlers.py`. Rename `NotificationMonitor` → `NotificationDispatcher`
   in `__main__.py`. Repoint `/admin_stats` pending-count to the new query.
6. **Tests** — delete obsolete files / cases; add the §5.2 set; verify
   `test_utils.py` and `test_handlers.py` still pass unchanged.
7. **Local validation** — `make lint`, `make test-quick`, then docker-compose
   smoke test against the staging Telegram bot (existing `bot.env` /
   `fetcher.env` already contain valid staging creds; do not touch).
8. **Plan-doc rewrite** — confirm this document still matches the shipped code;
   adjust if anything diverged.

Deployment to production Kubernetes is **out of scope** for this PR — handled as
a separate exercise after merge.

---

## 10. Local validation checklist

Run inside docker-compose with the staging bot creds:

- Add a fresh subscription. Trigger a force-refresh: confirm the user receives
  the "unchanged" reply (and the `Notifications` table shows a `kind =
  'force_refresh_unchanged'` row with `delivered_at NOT NULL`).
- Manually flip a row's `current_status` in `Applications` to a different value
  to simulate a transition; observe the next monitor tick triggers the consumer
  → enqueue → dispatcher → user receives a `kind = 'status_change'` message.
- Block the bot from a test account; trigger any notification path; confirm
  the corresponding `Users` row flips to `is_active = FALSE`,
  `deactivated_at` populated, the notification row stays pending. Unblock and
  send `/start` (or any input) → `is_active` flips back, the dispatcher delivers
  the backlog on the next tick.
- Stop the dispatcher process, generate a few notifications, restart it,
  confirm they all deliver.
- `/admin_stats` shows the right `inactive_users` and `pending_notifications`
  numbers (with reasonable oldest-age formatting).

---

## 11. Open decisions (resolved)

- **Render strategy:** pre-render text at enqueue time. Deferred render with a
  renderer registry rejected (§2.1).
- **Dedup / supersedence:** none in v1 (§2.2).
- **Producer wakeup signaling:** dispatcher exposes `wake()`; outbox writers
  call it after enqueue. The tick (`NOTIFY_MONITOR_TICK`) stays as a backstop
  for missed wakes / restart recovery (§4.2).
- **Reminders:** keep `ReminderMonitor` unchanged; durability is inherited via
  the existing reminder → fetch → consumer pipeline (§4.2).
- **`is_resolved` / `application_state` defensive rewrite on the unchanged
  path:** dropped (§6.1).
- **`notifier.py`:** deleted; helpers inlined to their single callers (§4.4).
- **Outbox cleanup:** inlined into the dispatcher loop. Delivered rows survive
  1 day, anything older than 30 days is purged unconditionally. No separate
  scheduler, no per-row `max_attempts` (the 30-day cap subsumes it) (§4.2, §8).

---

## 12. Out of scope

- Production Kubernetes deployment, image promotion, Helm rollout.
- Broadcast-message durability (admin-initiated; could go through the outbox
  later but doesn't share the system-initiated pain points).
- Renderer registry / deferred-render schema changes.
- Cross-message dedup / supersedence.
