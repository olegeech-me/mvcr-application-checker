import pytest

from conftest import make_db_with_mock_pool


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
