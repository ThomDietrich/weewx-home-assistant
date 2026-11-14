"""Derives the published part of a WeeWX archive record.

WeeWX archive records aggregate the LOOP packets of an interval, so
re-publishing one wholesale would overwrite the more recent real-time LOOP
values in Home Assistant. This module decides what an archive record
contributes: the observations that only archive records carry, plus the
rolling and cumulative aggregates read back out of the archive database.

Keeping it apart from the service class is what makes it testable without a
WeeWX engine -- every function here takes a database manager and a record and
returns a value.
"""

# Standard Python Libraries
from collections.abc import Callable
from functools import partial
import logging
from typing import Any

# Third-Party Libraries
from weeutil.weeutil import startOfDay  # type: ignore
import weewx.units  # type: ignore

logger = logging.getLogger(__name__)

# Observations exclusive to archive records -- absent or always None in LOOP
# packets (e.g. ET and windrun, or per-interval durations such as
# sunshineDur/rainDur added by services like weewx-sunrainduration). See the
# WeeWX docs on LOOP vs ARCHIVE. Extend as needed.
ARCHIVE_ONLY_MEASUREMENTS = frozenset({"ET", "windrun", "sunshineDur", "rainDur"})
# Bookkeeping fields kept in the filtered archive record so downstream unit
# conversion (to_std_system) still works. usUnits also has a sensor config, so it
# rides along as a published state value -- harmless, as it is identical to the
# value already published from LOOP packets.
ARCHIVE_PASSTHROUGH_KEYS = frozenset({"usUnits"})

# weewx-sunrainduration emits only the per-interval ``sunshineDur`` (seconds).
# Home Assistant users typically want "sunshine hours today", so we derive a
# cumulative daily total (in hours) from the WeeWX database and publish it as
# ``daySunshineDur``. Reading the day's sum from the DB (rather than keeping an
# in-memory counter) keeps the value correct across WeeWX restarts and resets
# automatically at local midnight. group_deltatime is numerically identical
# across unit systems, so to_std_system leaves the value untouched and the unit
# is overridden to "h" in the sensor metadata (sensors.yaml: daySunshineDur).
DAILY_SUNSHINE_SOURCE = "sunshineDur"
DAILY_SUNSHINE_KEY = "daySunshineDur"
ARCHIVE_DATA_BINDING = "wx_binding"

# Rolling / cumulative aggregates derived from the archive DB (like
# daySunshineDur) so Home Assistant needs no statistics/utility_meter helpers
# that starve when the station reports no change during dry periods.
HOUR_S = 3600
DAY_S = 86400
RAIN_EVENT_GAP_S = 6 * 3600  # >= 6 h dry separates rain events (MIT/IETD)
RAIN_EVENT_LOOKBACK_S = 30 * 86400

# Register unit groups for the aggregates derived below, so to_std_system
# converts them (US inch -> METRICWX mm) and getStandardUnitType yields the
# correct unit for discovery. WeeWX registers none of them, because none of
# them is a WeeWX observation: they exist only because this module computes
# them. The registration therefore lives here rather than in the service glue.
for _obs in ("hourRain", "rain24", "eventRain", "dayET"):
    weewx.units.obs_group_dict.setdefault(_obs, "group_rain")
for _obs in ("dayMaxOutTemp", "dayMinOutTemp"):
    weewx.units.obs_group_dict.setdefault(_obs, "group_temperature")
weewx.units.obs_group_dict.setdefault("dayMaxWindGust", "group_speed")


def select_publishable(record: dict[str, Any]) -> dict[str, Any] | None:
    """Return the part of an archive record worth publishing, or None.

    Only the archive-exclusive observations are kept, plus the bookkeeping keys
    downstream unit conversion needs. None means the record carries nothing an
    archive record is the only source of, so publishing it would just overwrite
    fresher LOOP values.
    """
    publishable = {
        key: value
        for key, value in record.items()
        if key in ARCHIVE_ONLY_MEASUREMENTS or key in ARCHIVE_PASSTHROUGH_KEYS
    }
    if (
        not ARCHIVE_ONLY_MEASUREMENTS.intersection(publishable)
        or "usUnits" not in publishable
    ):
        return None
    return publishable


def _attempt(label: str, compute: Callable[[], Any]) -> Any:
    """Run one aggregate's query, logging and yielding None if it fails.

    Each aggregate is attempted on its own, so a query that fails costs only
    its own value and is named in the log. One guard around the whole set used
    to discard every aggregate, including the ones already computed.
    """
    try:
        return compute()
    except Exception:
        logger.error("Failed to compute the %s aggregate", label, exc_info=True)
        return None


def _sum(manager: Any, table: str, column: str, start: float, end: float) -> float:
    """Return the sum of ``column`` over (start, end], zero when there are no rows."""
    row = manager.getSql(
        f"SELECT SUM({column}) FROM {table} "  # nosec B608
        "WHERE dateTime > ? AND dateTime <= ?",
        (start, end),
    )
    return row[0] if row and row[0] is not None else 0.0


def compute_event_rain(
    manager: Any, table: str, dt: float
) -> tuple[float, float | None, float | None]:
    """Return ``(total, start, end)`` of the most recent contiguous rain event.

    ``total`` is the summed rainfall; ``start`` and ``end`` are the epoch
    timestamps of the first and last measurable-rain records of that event.
    Rain records separated by >= RAIN_EVENT_GAP_S (6 h) without measurable
    rain belong to different events. Returns ``(0.0, None, None)`` when there
    was no rain within the lookback window.
    """
    # The query already excludes NULL rain, because SQL comparisons against
    # NULL are never true, so the rows need no further filtering.
    recs = [
        (row[0], row[1])
        for row in manager.genSql(
            f"SELECT dateTime, rain FROM {table} "  # nosec B608
            "WHERE rain > 0 AND dateTime > ? AND dateTime <= ? "
            "ORDER BY dateTime ASC",
            (dt - RAIN_EVENT_LOOKBACK_S, dt),
        )
    ]
    if not recs:
        return 0.0, None, None
    start = end = recs[-1][0]
    total = recs[-1][1]
    for i in range(len(recs) - 1, 0, -1):
        if recs[i][0] - recs[i - 1][0] >= RAIN_EVENT_GAP_S:
            break
        total += recs[i - 1][1]
        start = recs[i - 1][0]
    return total, start, end


def day_extreme(
    manager: Any,
    table: str,
    day_start: float,
    dt: float,
    *,
    highest: bool,
    column: str = "outTemp",
) -> tuple[float | None, float | None]:
    """Return ``(value, time)`` of today's highest or lowest ``column``.

    ``time`` is the epoch timestamp of the record holding that extreme.
    Returns ``(None, None)`` when the day has no usable ``column`` readings.
    """
    order = "DESC" if highest else "ASC"
    row = manager.getSql(
        f"SELECT {column}, dateTime FROM {table} "  # nosec B608
        f"WHERE dateTime > ? AND dateTime <= ? AND {column} IS NOT NULL "
        f"ORDER BY {column} {order}, dateTime ASC LIMIT 1",
        (day_start, dt),
    )
    if row and row[0] is not None:
        return row[0], row[1]
    return None, None


def _daily_sunshine(
    manager: Any, table: str, record: dict[str, Any], day_start: float, dt: float
) -> dict[str, Any]:
    """Return the cumulative daily sunshine, in hours, if the station reports it."""
    if record.get(DAILY_SUNSHINE_SOURCE) is None:
        return {}
    seconds = _attempt(
        DAILY_SUNSHINE_KEY,
        lambda: _sum(manager, table, DAILY_SUNSHINE_SOURCE, day_start, dt),
    )
    if seconds is None:
        return {}
    return {DAILY_SUNSHINE_KEY: seconds / HOUR_S}


def _rolling_sums(
    manager: Any, table: str, day_start: float, dt: float
) -> dict[str, Any]:
    """Return the rolling rain totals and the day's evapotranspiration."""
    sums: dict[str, Any] = {}
    for key, column, start in (
        ("hourRain", "rain", dt - HOUR_S),
        ("rain24", "rain", dt - DAY_S),
        ("dayET", "ET", day_start),
    ):
        # partial rather than a lambda over the loop variables. A lambda
        # would be correct here, because _attempt calls it inside this same
        # iteration, but only for that reason; partial binds the values and
        # does not depend on when it is called.
        total = _attempt(key, partial(_sum, manager, table, column, start, dt))
        if total is not None:
            sums[key] = total
    return sums


def _rain_event(
    manager: Any, table: str, record: dict[str, Any], dt: float
) -> dict[str, Any]:
    """Return the most recent rain event, bracketed by its start and end."""
    event = _attempt("eventRain", lambda: compute_event_rain(manager, table, dt))
    if event is None:
        return {}
    total, start, end = event
    bracketed: dict[str, Any] = {"eventRain": total}
    if start is None or end is None:
        return bracketed
    # The first record of an event reports the rain of the interval ending at
    # its timestamp, so the event began one interval earlier.
    interval_s = (record.get("interval") or 0) * 60
    bracketed["eventRainStart"] = start
    bracketed["eventRainEnd"] = end
    bracketed["eventRainDuration"] = (end - start + interval_s) / 60.0
    return bracketed


def _daily_extremes(
    manager: Any, table: str, day_start: float, dt: float
) -> dict[str, Any]:
    """Return today's temperature high and low and its strongest gust, each timed."""
    extremes: dict[str, Any] = {}
    for key, column, highest in (
        ("dayMaxOutTemp", "outTemp", True),
        ("dayMinOutTemp", "outTemp", False),
        ("dayMaxWindGust", "windGust", True),
    ):
        # partial rather than a lambda, for the reason given in _rolling_sums.
        extreme = _attempt(
            key,
            partial(
                day_extreme,
                manager,
                table,
                day_start,
                dt,
                highest=highest,
                column=column,
            ),
        )
        if extreme is None:
            continue
        value, when = extreme
        if value is not None:
            extremes[key] = value
            extremes[f"{key}Time"] = when
    return extremes


def derive_from_db(db_binder: Any, record: dict[str, Any]) -> dict[str, Any]:
    """Return the aggregates for ``record`` read back out of the archive DB.

    All values are summed from the WeeWX archive database (authoritative and
    restart-safe), so Home Assistant needs no statistics/utility_meter helpers
    that would starve when the station reports no change:
      - daySunshineDur: cumulative daily sunshine, in hours (from sunshineDur).
      - hourRain / rain24: rolling rainfall over the last 1 h / 24 h.
      - dayET: cumulative evapotranspiration since local midnight.
      - eventRain: rainfall of the most recent event (events separated by a
        >= 6 h dry period, the Minimum Inter-event Time), with eventRainStart,
        eventRainEnd (timestamps) and eventRainDuration (minutes).
      - dayMaxOutTemp / dayMinOutTemp: today's high/low outdoor temperature,
        each with the time it occurred (dayMaxOutTempTime / dayMinOutTempTime).
      - dayMaxWindGust: today's strongest wind gust, with its time
        (dayMaxWindGustTime).
    Rain/ET/temperature/wind values stay in the record's unit system;
    to_std_system converts them downstream. Every aggregate is attempted on its
    own, so a failing query costs that one value and is logged by name; a
    record without a timestamp, or an unreachable database, yields nothing.
    """
    dt = record.get("dateTime")
    if dt is None:
        return {}
    try:
        manager = db_binder.get_manager(ARCHIVE_DATA_BINDING)
        table = manager.table_name
    except Exception:
        logger.error(
            "Cannot reach the archive database; publishing no DB-derived aggregates",
            exc_info=True,
        )
        return {}
    day_start = startOfDay(dt)
    return {
        **_daily_sunshine(manager, table, record, day_start, dt),
        **_rolling_sums(manager, table, day_start, dt),
        **_rain_event(manager, table, record, dt),
        **_daily_extremes(manager, table, day_start, dt),
    }
