import re

from prometheus_client import generate_latest

from fetcher.prometheus_metrics import new_test_metrics


def _last_success_value(payload: str) -> float:
    match = re.search(
        r"^mvcr_fetcher_last_success_timestamp_seconds ([0-9.eE+-]+)$",
        payload,
        re.MULTILINE,
    )
    assert match, payload
    return float(match.group(1))


def test_last_success_absent_until_first_mark_success():
    metrics = new_test_metrics()
    registry = metrics._registry

    before = generate_latest(registry).decode()
    assert "mvcr_fetcher_last_success_timestamp_seconds" not in before

    metrics.mark_success(1_700_000_000.0)
    assert _last_success_value(generate_latest(registry).decode()) == 1_700_000_000.0

    metrics.mark_success(1_700_000_100.0)
    assert _last_success_value(generate_latest(registry).decode()) == 1_700_000_100.0
