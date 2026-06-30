"""Tests for the derived daily sunshine total (daySunshineDur, in hours)."""

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

    def __init__(self, total):
        self._total = total
        self.calls = []

    def getSql(self, sql, args):
        self.calls.append((sql, args))
        return (self._total,)


def _stub_controller(total):
    """Build a stub exposing only the engine.db_binder surface the method uses."""
    manager = _FakeManager(total)
    binder = SimpleNamespace(get_manager=lambda binding: manager)
    stub = SimpleNamespace(engine=SimpleNamespace(db_binder=binder))
    return stub, manager


def test_daily_sunshine_added_in_hours():
    """The day's sum (seconds) is published as daySunshineDur in hours."""
    stub, manager = _stub_controller(18000.0)  # 5 hours of sunshine
    record = {"dateTime": 1782826200, DAILY_SUNSHINE_SOURCE: 240.0, "usUnits": 1}
    filtered = {DAILY_SUNSHINE_SOURCE: 240.0, "usUnits": 1}

    Controller._augment_daily_sunshine(stub, record, filtered)

    assert filtered[DAILY_SUNSHINE_KEY] == pytest.approx(5.0)
    # The query is scoped to the source field and bounded by the record's day.
    sql, args = manager.calls[0]
    assert DAILY_SUNSHINE_SOURCE in sql
    assert args[1] == record["dateTime"]
    assert args[0] < args[1]  # day_start precedes the record


def test_daily_sunshine_skipped_when_source_absent():
    """Records without sunshineDur (no add-on) are left untouched."""
    stub, _ = _stub_controller(0.0)
    record = {"dateTime": 1782826200, "usUnits": 1}
    filtered = {"ET": 0.1, "usUnits": 1}

    Controller._augment_daily_sunshine(stub, record, filtered)

    assert DAILY_SUNSHINE_KEY not in filtered


def test_daily_sunshine_null_sum_is_zero():
    """A NULL SUM (no rows yet today) yields 0.0 hours rather than failing."""
    stub, _ = _stub_controller(None)
    record = {"dateTime": 1782826200, DAILY_SUNSHINE_SOURCE: 0.0, "usUnits": 1}
    filtered = {DAILY_SUNSHINE_SOURCE: 0.0, "usUnits": 1}

    Controller._augment_daily_sunshine(stub, record, filtered)

    assert filtered[DAILY_SUNSHINE_KEY] == 0.0


def test_daily_sunshine_db_error_is_swallowed():
    """A DB error must not break archive publishing; just skip the field."""

    def boom(binding):
        raise RuntimeError("db down")

    stub = SimpleNamespace(
        engine=SimpleNamespace(db_binder=SimpleNamespace(get_manager=boom))
    )
    record = {"dateTime": 1782826200, DAILY_SUNSHINE_SOURCE: 10.0, "usUnits": 1}
    filtered = {DAILY_SUNSHINE_SOURCE: 10.0, "usUnits": 1}

    Controller._augment_daily_sunshine(stub, record, filtered)

    assert DAILY_SUNSHINE_KEY not in filtered
