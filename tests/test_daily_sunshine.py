"""Tests for the DB-derived archive aggregates (daySunshineDur, rolling rain, ET)."""

# Standard Python Libraries
from types import SimpleNamespace

# Third-Party Libraries
import pytest

from weewx_ha.controller import (
    DAILY_SUNSHINE_KEY,
    DAILY_SUNSHINE_SOURCE,
    Controller,
)


class _FakeManager:
    """Minimal stand-in for a WeeWX database manager."""

    table_name = "archive"

    def __init__(self, sums=None, event_rows=None):
        self._sums = sums or {}
        self._event_rows = event_rows or []

    def getSql(self, sql, args):
        start, end = args
        if "sunshineDur" in sql:
            return (self._sums.get("sunshine"),)
        if "SUM(ET)" in sql:
            return (self._sums.get("dayET"),)
        if "SUM(rain)" in sql:
            # hourRain and rain24 share the column; distinguish by window length.
            return (
                (self._sums.get("hourRain"),)
                if (end - start) == 3600
                else (self._sums.get("rain24"),)
            )
        return (None,)

    def genSql(self, sql, args):
        return iter(self._event_rows)


def _stub(manager):
    binder = SimpleNamespace(get_manager=lambda binding: manager)
    stub = SimpleNamespace(engine=SimpleNamespace(db_binder=binder))
    stub._compute_event_rain = Controller._compute_event_rain
    return stub


def test_augment_db_derived_adds_all_aggregates():
    dt = 1782826200
    mgr = _FakeManager(
        sums={"sunshine": 18000.0, "hourRain": 0.02, "rain24": 0.5, "dayET": 0.1},
        event_rows=[(dt - 1000, 0.1), (dt - 500, 0.2)],  # one contiguous event
    )
    record = {"dateTime": dt, DAILY_SUNSHINE_SOURCE: 240.0, "ET": 0.01, "usUnits": 1}
    filtered = {"ET": 0.01, "usUnits": 1}

    Controller._augment_db_derived(_stub(mgr), record, filtered)

    assert filtered[DAILY_SUNSHINE_KEY] == pytest.approx(5.0)  # 18000 s / 3600
    assert filtered["hourRain"] == pytest.approx(0.02)
    assert filtered["rain24"] == pytest.approx(0.5)
    assert filtered["dayET"] == pytest.approx(0.1)
    assert filtered["eventRain"] == pytest.approx(0.3)  # 0.1 + 0.2, contiguous


def test_augment_db_derived_without_sunshine_source():
    """No sunshineDur -> no daySunshineDur, but rain/ET aggregates still added."""
    dt = 1782826200
    mgr = _FakeManager(sums={"hourRain": 0.0, "rain24": 0.0, "dayET": 0.0})
    record = {"dateTime": dt, "ET": 0.0, "usUnits": 1}
    filtered = {"ET": 0.0, "usUnits": 1}

    Controller._augment_db_derived(_stub(mgr), record, filtered)

    assert DAILY_SUNSHINE_KEY not in filtered
    assert filtered["hourRain"] == 0.0
    assert filtered["dayET"] == 0.0
    assert filtered["eventRain"] == 0.0


def test_augment_db_derived_null_sums_are_zero():
    dt = 1782826200
    mgr = _FakeManager(sums={})  # all SUMs return None (no rows)
    record = {"dateTime": dt, "ET": 0.0, "usUnits": 1}
    filtered = {"ET": 0.0, "usUnits": 1}

    Controller._augment_db_derived(_stub(mgr), record, filtered)

    assert filtered["hourRain"] == 0.0
    assert filtered["rain24"] == 0.0
    assert filtered["dayET"] == 0.0


def test_event_rain_only_most_recent_event():
    dt = 1_000_000
    # Old event (0.5) then a >6 h dry gap, then the current event (0.3 + 0.4).
    rows = [(dt - 30000, 0.5), (dt - 100, 0.3), (dt - 50, 0.4)]
    total = Controller._compute_event_rain(_FakeManager(event_rows=rows), "archive", dt)
    assert total == pytest.approx(0.7)  # 30000 s > 21600 s (6 h) -> old event excluded


def test_event_rain_merges_within_gap():
    dt = 1_000_000
    # All within 6 h of each other -> one event.
    rows = [(dt - 10000, 0.2), (dt - 5000, 0.3), (dt - 100, 0.5)]
    total = Controller._compute_event_rain(_FakeManager(event_rows=rows), "archive", dt)
    assert total == pytest.approx(1.0)


def test_event_rain_empty_is_zero():
    total = Controller._compute_event_rain(_FakeManager(event_rows=[]), "archive", 1000)
    assert total == 0.0
