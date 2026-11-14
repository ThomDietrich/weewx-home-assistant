"""Tests for the DB-derived archive aggregates (daySunshineDur, rolling rain, ET)."""

# Standard Python Libraries
import logging
from types import SimpleNamespace

# Third-Party Libraries
import pytest

# Geekpad Libraries
from weewx_ha.archive import (
    DAILY_SUNSHINE_KEY,
    DAILY_SUNSHINE_SOURCE,
    compute_event_rain,
    day_extreme,
    derive_from_db,
)


class _FakeManager:
    """Minimal stand-in for a WeeWX database manager."""

    table_name = "archive"

    def __init__(self, sums=None, event_rows=None, extremes=None):
        self._sums = sums or {}
        self._event_rows = event_rows or []
        self._extremes = extremes or {}

    def getSql(self, sql, args):
        start, end = args
        if "sunshineDur" in sql:
            return (self._sums.get("sunshine"),)
        if "outTemp" in sql or "windGust" in sql:
            col = "windGust" if "windGust" in sql else "outTemp"
            return self._extremes.get(f"{col}_{'max' if 'DESC' in sql else 'min'}")
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


def _binder(manager):
    """Stand in for engine.db_binder, handing back the one fake manager."""
    return SimpleNamespace(get_manager=lambda binding: manager)


def test_derive_from_db_adds_all_aggregates():
    """Every DB-derived aggregate lands in the returned mapping."""
    dt = 1782826200
    mgr = _FakeManager(
        sums={"sunshine": 18000.0, "hourRain": 0.02, "rain24": 0.5, "dayET": 0.1},
        event_rows=[(dt - 1000, 0.1), (dt - 500, 0.2)],  # one contiguous event
        extremes={
            "outTemp_max": (25.0, dt - 3600),
            "outTemp_min": (12.0, dt - 7200),
            "windGust_max": (48.0, dt - 1800),
        },
    )
    record = {
        "dateTime": dt,
        DAILY_SUNSHINE_SOURCE: 240.0,
        "ET": 0.01,
        "interval": 5,
        "usUnits": 1,
    }

    derived = derive_from_db(_binder(mgr), record)

    assert derived[DAILY_SUNSHINE_KEY] == pytest.approx(5.0)  # 18000 s / 3600
    assert derived["hourRain"] == pytest.approx(0.02)
    assert derived["rain24"] == pytest.approx(0.5)
    assert derived["dayET"] == pytest.approx(0.1)
    assert derived["eventRain"] == pytest.approx(0.3)  # 0.1 + 0.2, contiguous
    assert derived["eventRainStart"] == dt - 1000
    assert derived["eventRainEnd"] == dt - 500
    # span (500 s) + one 5-min archive interval (300 s), in minutes
    assert derived["eventRainDuration"] == pytest.approx((500 + 300) / 60)
    assert derived["dayMaxOutTemp"] == 25.0
    assert derived["dayMaxOutTempTime"] == dt - 3600
    assert derived["dayMinOutTemp"] == 12.0
    assert derived["dayMinOutTempTime"] == dt - 7200
    assert derived["dayMaxWindGust"] == 48.0
    assert derived["dayMaxWindGustTime"] == dt - 1800


def test_derive_from_db_without_sunshine_source():
    """No sunshineDur -> no daySunshineDur, but rain/ET aggregates still added."""
    dt = 1782826200
    mgr = _FakeManager(sums={"hourRain": 0.0, "rain24": 0.0, "dayET": 0.0})
    record = {"dateTime": dt, "ET": 0.0, "usUnits": 1}

    derived = derive_from_db(_binder(mgr), record)

    assert DAILY_SUNSHINE_KEY not in derived
    assert derived["hourRain"] == 0.0
    assert derived["dayET"] == 0.0
    assert derived["eventRain"] == 0.0
    assert "eventRainStart" not in derived  # no event -> no timestamps


def test_derive_from_db_null_sums_are_zero():
    """A SUM over no rows publishes zero rather than None."""
    dt = 1782826200
    mgr = _FakeManager(sums={})  # all SUMs return None (no rows)
    record = {"dateTime": dt, "ET": 0.0, "usUnits": 1}

    derived = derive_from_db(_binder(mgr), record)

    assert derived["hourRain"] == 0.0
    assert derived["rain24"] == 0.0
    assert derived["dayET"] == 0.0


def test_derive_from_db_without_a_timestamp():
    """A record with no dateTime has no window to aggregate over."""
    assert derive_from_db(_binder(_FakeManager()), {"ET": 0.0}) == {}


def test_derive_from_db_with_an_unusable_timestamp(caplog):
    """A dateTime that is not an epoch costs the aggregates, nothing more.

    startOfDay goes through time.localtime, so a record carrying something
    else raises there. The caller is a WeeWX event callback running on the
    engine's own thread, where an escaping exception ends weewxd -- so this
    has to come back as "no aggregates", logged, like an unreachable database.
    """
    record = {"dateTime": "not-an-epoch", "ET": 0.0, "usUnits": 1}

    with caplog.at_level(logging.ERROR, logger="weewx_ha"):
        assert derive_from_db(_binder(_FakeManager()), record) == {}

    assert [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_a_malformed_interval_costs_only_the_rain_event(caplog):
    """The event bracketing reads the record, so it fails like a query does.

    eventRainDuration is computed from the record's own ``interval``. That
    arithmetic belongs to the aggregate and is attempted with it, so a record
    whose interval is not a number loses the rain event and keeps the rest.
    """
    dt = 1782826200
    mgr = _FakeManager(
        sums={"hourRain": 0.02, "rain24": 0.5, "dayET": 0.1},
        event_rows=[(dt - 1000, 0.1), (dt - 500, 0.2)],
    )
    record = {"dateTime": dt, "ET": 0.0, "interval": "five", "usUnits": 1}

    with caplog.at_level(logging.ERROR, logger="weewx_ha"):
        derived = derive_from_db(_binder(mgr), record)

    assert "eventRain" not in derived
    assert derived["hourRain"] == pytest.approx(0.02)
    assert derived["dayET"] == pytest.approx(0.1)
    assert any("eventRain" in r.getMessage() for r in caplog.records), [
        r.getMessage() for r in caplog.records
    ]


def test_event_rain_only_most_recent_event():
    """A dry gap of at least the MIT ends an event, excluding older rain."""
    dt = 1_000_000
    # Old event (0.5) then a >6 h dry gap, then the current event (0.3 + 0.4).
    rows = [(dt - 30000, 0.5), (dt - 100, 0.3), (dt - 50, 0.4)]
    total, start, end = compute_event_rain(_FakeManager(event_rows=rows), "archive", dt)
    assert total == pytest.approx(0.7)  # 30000 s > 21600 s (6 h) -> old event excluded
    assert start == dt - 100  # event begins at the first record after the gap
    assert end == dt - 50


def test_event_rain_merges_within_gap():
    """Showers closer together than the MIT count as a single event."""
    dt = 1_000_000
    # All within 6 h of each other -> one event.
    rows = [(dt - 10000, 0.2), (dt - 5000, 0.3), (dt - 100, 0.5)]
    total, start, end = compute_event_rain(_FakeManager(event_rows=rows), "archive", dt)
    assert total == pytest.approx(1.0)
    assert start == dt - 10000
    assert end == dt - 100


def test_event_rain_empty_is_zero():
    """With no rain rows the event total is zero and carries no bounds."""
    total, start, end = compute_event_rain(_FakeManager(event_rows=[]), "archive", 1000)
    assert total == 0.0
    assert start is None
    assert end is None


def test_day_extreme_returns_value_and_time():
    """A day extreme comes back with the timestamp at which it occurred."""
    dt = 1_000_000
    mgr = _FakeManager(
        extremes={
            "outTemp_max": (25.0, dt - 3600),
            "outTemp_min": (12.0, dt - 7200),
            "windGust_max": (48.0, dt - 1800),
        }
    )
    assert day_extreme(mgr, "archive", dt - 86400, dt, highest=True) == (
        25.0,
        dt - 3600,
    )
    assert day_extreme(mgr, "archive", dt - 86400, dt, highest=False) == (
        12.0,
        dt - 7200,
    )
    assert day_extreme(
        mgr, "archive", dt - 86400, dt, highest=True, column="windGust"
    ) == (48.0, dt - 1800)


def test_day_extreme_none_when_empty():
    """A day with no rows yields neither a value nor a timestamp."""
    mgr = _FakeManager(extremes={})
    assert day_extreme(mgr, "archive", 0, 1000, highest=True) == (None, None)


class _RainQueryFails(_FakeManager):
    """A manager whose rolling-rain query is the only one that fails."""

    def getSql(self, sql, args):
        if "SUM(rain)" in sql:
            raise RuntimeError("no such column: rain")
        return super().getSql(sql, args)


def test_one_failing_query_costs_only_its_own_aggregate(caplog):
    """A broken rain query must not take the other aggregates down with it.

    The rolling rain sums are computed first, so a single guard around the
    whole set discarded every aggregate after them -- the day's ET, the rain
    event and all three day extremes -- for one failing column.
    """
    dt = 1782826200
    mgr = _RainQueryFails(
        sums={"dayET": 0.1},
        event_rows=[(dt - 500, 0.2)],
        extremes={"outTemp_max": (25.0, dt - 3600)},
    )

    with caplog.at_level(logging.ERROR, logger="weewx_ha"):
        derived = derive_from_db(_binder(mgr), {"dateTime": dt, "usUnits": 1})

    assert "hourRain" not in derived, "the failing aggregate must be absent"
    assert "rain24" not in derived
    assert derived["dayET"] == pytest.approx(0.1), "ET survived the rain failure"
    assert derived["eventRain"] == pytest.approx(0.2)
    assert derived["dayMaxOutTemp"] == 25.0
    # Both failures are reported, each naming the aggregate that could not be
    # computed, so the gap in the published data is traceable.
    failures = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("hourRain" in m for m in failures), failures
    assert any("rain24" in m for m in failures), failures
