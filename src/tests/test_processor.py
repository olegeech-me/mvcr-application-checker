import pytest

from conftest import make_processor


# ---------------------------------------------------------------------------
# Error message generation
# ---------------------------------------------------------------------------


def test_processor_generate_error_message_oam():
    proc = make_processor()
    app_details = {"number": "12345", "suffix": "0", "type": "TP", "year": "2023"}
    result = proc._generate_error_message(app_details)
    assert result == "OAM-12345-0/TP-2023 ERROR"


def test_processor_generate_error_message_zov():
    proc = make_processor()
    app_details = {
        "number": "ISTA202504220001",
        "suffix": "0",
        "type": "ZOV",
        "year": 0,
        "source": "zov",
    }
    result = proc._generate_error_message(app_details)
    assert result == "ISTA202504220001 ERROR"


# ---------------------------------------------------------------------------
# Processing lock lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_processor_lock_lifecycle():
    """start -> is_processing -> end -> not processing"""
    proc = make_processor()

    assert await proc.is_processing("fetch", "12345", "TP", 2023) is False
    await proc.start_processing("fetch", "12345", "TP", 2023)
    assert await proc.is_processing("fetch", "12345", "TP", 2023) is True
    await proc.end_processing("fetch", "12345", "TP", 2023)
    assert await proc.is_processing("fetch", "12345", "TP", 2023) is False


@pytest.mark.asyncio
async def test_processor_refresh_checks_both_queues():
    """refresh is_processing returns True if app is in either fetch or refresh queue"""
    proc = make_processor()

    await proc.start_processing("fetch", "12345", "TP", 2023)
    assert await proc.is_processing("refresh", "12345", "TP", 2023) is True

    await proc.end_processing("fetch", "12345", "TP", 2023)
    await proc.start_processing("refresh", "12345", "TP", 2023)
    assert await proc.is_processing("refresh", "12345", "TP", 2023) is True
