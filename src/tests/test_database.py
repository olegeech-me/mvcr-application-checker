import pytest
from pathlib import Path
from unittest.mock import patch

from bot.monitor import compute_next_retry_at
from conftest import make_db_with_mock_pool


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_applications_current_status_schema_is_unbounded_text():
    """MVCR resolved/pre-approved responses can exceed 1000 characters."""
    init_sql = (REPO_ROOT / "db-init-scripts" / "init.sql").read_text()
    helm_init = (
        REPO_ROOT
        / "deploy"
        / "mvcr-application-checker-helm"
        / "templates"
        / "postgresql"
        / "configmap-init.yaml"
    ).read_text()

    assert "current_status TEXT DEFAULT 'Unknown'" in init_sql
    assert "current_status TEXT DEFAULT 'Unknown'" in helm_init

    migrations_dir = REPO_ROOT / "db-migrations"
    migration_sql = "\n".join(path.read_text() for path in migrations_dir.glob("*.sql"))
    assert "ALTER COLUMN current_status TYPE TEXT" in migration_sql


@pytest.mark.asyncio
async def test_db_insert_application_duplicate():
    """insert_application returns False on UniqueViolationError"""
    import asyncpg

    db, conn = make_db_with_mock_pool()
    conn.execute.side_effect = asyncpg.UniqueViolationError("")
    result = await db.insert_application(
        chat_id=100,
        application_number="4242",
        application_suffix="0",
        application_type="TP",
        application_year=2042,
    )
    assert result is False


@pytest.mark.asyncio
async def test_db_fetch_user_subscriptions():
    """fetch_user_subscriptions (SELECT *) returns dicts"""
    db, conn = make_db_with_mock_pool()
    fake_row = {
        "application_id": 1,
        "user_id": 1,
        "application_number": "4242",
        "application_suffix": "0",
        "application_type": "TP",
        "application_year": 2042,
        "current_status": "Unknown",
        "application_state": "UNKNOWN",
        "is_resolved": False,
    }
    conn.fetch.return_value = [fake_row]
    rows = await db.fetch_user_subscriptions(chat_id=100)
    assert len(rows) == 1
    assert rows[0]["application_type"] == "TP"


# ---------------------------------------------------------------------------
# User activity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_mark_user_inactive_idempotent():
    """mark_user_inactive returns True only when the row was actually flipped;
    repeated calls on an already-inactive user return False (no-op)
    """
    db, conn = make_db_with_mock_pool()

    conn.execute.return_value = "UPDATE 1"
    assert await db.mark_user_inactive(100, "blocked") is True

    conn.execute.return_value = "UPDATE 0"
    assert await db.mark_user_inactive(100, "blocked") is False


@pytest.mark.asyncio
async def test_db_mark_user_inactive_query_uses_guard():
    """The UPDATE must be guarded by `is_active = TRUE` so it's a true no-op
    on already-inactive users (otherwise deactivated_at would tick on every retry)
    """
    db, conn = make_db_with_mock_pool()
    conn.execute.return_value = "UPDATE 1"
    await db.mark_user_inactive(100, "blocked")

    sql = conn.execute.call_args[0][0]
    assert "UPDATE Users" in sql
    assert "is_active = FALSE" in sql
    assert "is_active = TRUE" in sql, "missing guard, would re-stamp deactivated_at"


@pytest.mark.asyncio
async def test_db_reactivate_user_if_needed_idempotent():
    db, conn = make_db_with_mock_pool()

    conn.execute.return_value = "UPDATE 0"
    assert await db.reactivate_user_if_needed(100) is False

    conn.execute.return_value = "UPDATE 1"
    assert await db.reactivate_user_if_needed(100) is True


@pytest.mark.asyncio
async def test_db_reactivate_user_query_uses_guard():
    """Guard `is_active = FALSE` makes this a no-op for active users (the common case)"""
    db, conn = make_db_with_mock_pool()
    conn.execute.return_value = "UPDATE 0"
    await db.reactivate_user_if_needed(100)

    sql = conn.execute.call_args[0][0]
    assert "is_active = TRUE" in sql
    assert "is_active = FALSE" in sql, "missing guard, every interaction would write"


@pytest.mark.asyncio
async def test_db_count_inactive_users():
    db, conn = make_db_with_mock_pool()
    conn.fetchval.return_value = 7
    assert await db.count_inactive_users() == 7
    sql = conn.fetchval.call_args[0][0]
    assert "is_active = FALSE" in sql


# ---------------------------------------------------------------------------
# Background fetchers must skip inactive users
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_fetch_applications_needing_update_filters_inactive_users():
    """Background refresh loop must not target users that have blocked the bot"""
    import datetime
    db, conn = make_db_with_mock_pool()
    conn.fetch.return_value = []
    await db.fetch_applications_needing_update(
        datetime.timedelta(seconds=300), datetime.timedelta(seconds=3600),
    )
    sql = conn.fetch.call_args[0][0]
    assert "u.is_active = TRUE" in sql


@pytest.mark.asyncio
async def test_db_fetch_applications_to_expire_filters_inactive_users():
    import datetime
    db, conn = make_db_with_mock_pool()
    conn.fetch.return_value = []
    await db.fetch_applications_to_expire(datetime.timedelta(days=30))
    sql = conn.fetch.call_args[0][0]
    assert "u.is_active = TRUE" in sql


@pytest.mark.asyncio
async def test_db_fetch_due_reminders_filters_inactive_users():
    db, conn = make_db_with_mock_pool()
    conn.fetch.return_value = []
    await db.fetch_due_reminders()
    sql = conn.fetch.call_args[0][0]
    assert "u.is_active = TRUE" in sql


@pytest.mark.asyncio
async def test_db_fetch_all_chat_ids_filters_inactive_users():
    """Broadcast destination list must not include blocked users"""
    db, conn = make_db_with_mock_pool()
    conn.fetch.return_value = []
    await db.fetch_all_chat_ids()
    sql = conn.fetch.call_args[0][0]
    assert "is_active = TRUE" in sql


# ---------------------------------------------------------------------------
# Notifications outbox
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_enqueue_notification_returns_new_id():
    """Outbox writers fire-and-forget; the returned id is used as the
    in-process handle (logging, dedup) and must round-trip from RETURNING id
    """
    db, conn = make_db_with_mock_pool()
    conn.fetchval.return_value = 4242

    new_id = await db.enqueue_notification(
        chat_id=100, kind="status_change", text="hello", origin_ref=42,
    )
    assert new_id == 4242

    args = conn.fetchval.call_args[0]
    sql = args[0]
    assert "INSERT INTO Notifications" in sql
    assert "chat_id" in sql and "kind" in sql and "text" in sql and "origin_ref" in sql
    assert "RETURNING id" in sql
    assert args[1:] == (100, "status_change", "hello", 42)


@pytest.mark.asyncio
async def test_db_claim_due_notifications_uses_skip_locked_and_active_filter():
    """Concurrent dispatcher safety + dead-user shielding — both load-bearing.
    SKIP LOCKED prevents double-claim across workers; is_active=TRUE keeps
    blocked-user rows out of the pool until reactivation re-exposes them
    """
    db, conn = make_db_with_mock_pool()
    conn.fetch.return_value = []
    await db.claim_due_notifications(limit=50, lock_window_seconds=300)
    args = conn.fetch.call_args[0]
    sql = args[0]
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "u.is_active = TRUE" in sql
    assert "delivered_at IS NULL" in sql
    assert "next_attempt_at <= CURRENT_TIMESTAMP" in sql
    assert "next_attempt_at = CURRENT_TIMESTAMP + ($2 * INTERVAL '1 second')" in sql
    assert args[1:] == (50, 300)


@pytest.mark.asyncio
async def test_db_mark_delivered_sets_delivered_at_and_records_optional_error():
    """`permanent_other` reuses mark_delivered to break the loop; last_error
    must be persisted so we can postmortem why the row was abandoned
    """
    db, conn = make_db_with_mock_pool()
    conn.execute.return_value = "UPDATE 1"
    assert await db.mark_delivered(42, last_error="permanent_other") is True
    sql = conn.execute.call_args[0][0]
    assert "delivered_at = CURRENT_TIMESTAMP" in sql
    assert "last_error = $2" in sql


@pytest.mark.asyncio
async def test_db_bump_attempt_persists_caller_supplied_timestamp():
    """The backoff policy lives in monitor.compute_next_retry_at; this method
    just persists the timestamp the caller computed, atomically with the
    counter increment — keeps SQL trivial
    """
    import datetime as _dt
    db, conn = make_db_with_mock_pool()
    conn.execute.return_value = "UPDATE 1"
    next_at = _dt.datetime(2026, 1, 1, 12, 0, 0)
    await db.bump_attempt(42, next_at, last_error="retryable_gave_up")
    args = conn.execute.call_args[0]
    sql = args[0]
    assert "attempts = attempts + 1" in sql
    assert "next_attempt_at = $2" in sql
    assert args[1] == 42
    assert args[2] == next_at
    assert args[3] == "retryable_gave_up"


@pytest.mark.asyncio
async def test_db_bump_attempt_accepts_compute_next_retry_at_output():
    """Production path: dispatcher computes backoff, DB persists it — the
    timestamp must survive asyncpg encoding for TIMESTAMP columns
    """
    db, conn = make_db_with_mock_pool()
    conn.execute.return_value = "UPDATE 1"

    next_at = compute_next_retry_at(3, 300, 3600)
    assert next_at.tzinfo is None

    assert await db.bump_attempt(42, next_at, last_error="retryable_gave_up") is True
    assert conn.execute.call_args[0][2] is next_at


@pytest.mark.asyncio
async def test_db_bump_attempt_records_metric_on_failure():
    import datetime as _dt
    db, conn = make_db_with_mock_pool()
    conn.execute.side_effect = TypeError(
        "invalid input for query argument $2: can't subtract offset-naive and offset-aware datetimes"
    )
    with patch("bot.database.prometheus_metrics.record_error") as record_error:
        result = await db.bump_attempt(
            181,
            _dt.datetime(2026, 6, 24, 6, 6, 38),
            last_error="retryable_gave_up",
        )
    assert result is False
    record_error.assert_called_once_with("db", "db_error")


@pytest.mark.asyncio
async def test_db_purge_old_notifications_drops_both_classes():
    """Single DELETE must drop (a) delivered rows past the retention window
    AND (b) any row past the absolute max age — that's what gives the table
    its hard upper bound
    """
    db, conn = make_db_with_mock_pool()
    conn.execute.return_value = "DELETE 7"
    deleted = await db.purge_old_notifications(
        delivered_retention_days=1, pending_max_age_days=30,
    )
    assert deleted == 7
    args = conn.execute.call_args[0]
    sql = args[0]
    assert "DELETE FROM Notifications" in sql
    assert "delivered_at IS NOT NULL" in sql
    assert "delivered_at < CURRENT_TIMESTAMP - ($1 * INTERVAL '1 day')" in sql
    assert "created_at < CURRENT_TIMESTAMP - ($2 * INTERVAL '1 day')" in sql
    assert args[1:] == (1, 30)


@pytest.mark.asyncio
async def test_db_count_pending_notifications_filters_inactive_users():
    """Inactive users' pending rows are temporarily off-limits to the dispatcher
    and must not inflate the visible backlog in /admin_stats
    """
    db, conn = make_db_with_mock_pool()
    conn.fetchrow.return_value = {"pending": 0, "oldest_age": 0}
    pending, age = await db.count_pending_notifications()
    assert (pending, age) == (0, 0)
    sql = conn.fetchrow.call_args[0][0]
    assert "FROM Notifications n" in sql
    assert "u.is_active = TRUE" in sql
    assert "delivered_at IS NULL" in sql
