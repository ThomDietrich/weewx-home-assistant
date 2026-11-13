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
    to_std_system converts them downstream. A record without a timestamp
    yields nothing, and a failure yields whatever was computed before it
    (logged).
    """
    derived: dict[str, Any] = {}
    dt = record.get("dateTime")
    if dt is None:
        return derived
    try:
        manager = db_binder.get_manager(ARCHIVE_DATA_BINDING)
        table = manager.table_name
        day_start = startOfDay(dt)

        if record.get(DAILY_SUNSHINE_SOURCE) is not None:
            derived[DAILY_SUNSHINE_KEY] = (
                _sum(manager, table, DAILY_SUNSHINE_SOURCE, day_start, dt) / HOUR_S
            )
        derived["hourRain"] = _sum(manager, table, "rain", dt - HOUR_S, dt)
        derived["rain24"] = _sum(manager, table, "rain", dt - DAY_S, dt)
        derived["dayET"] = _sum(manager, table, "ET", day_start, dt)

        event_total, event_start, event_end = compute_event_rain(manager, table, dt)
        derived["eventRain"] = event_total
        if event_start is not None and event_end is not None:
            # The first record of an event reports the rain of the interval
            # ending at its timestamp, so the event began one interval earlier.
            interval_s = (record.get("interval") or 0) * 60
            derived["eventRainStart"] = event_start
            derived["eventRainEnd"] = event_end
            derived["eventRainDuration"] = (event_end - event_start + interval_s) / 60.0

        for key, column, highest in (
            ("dayMaxOutTemp", "outTemp", True),
            ("dayMinOutTemp", "outTemp", False),
            ("dayMaxWindGust", "windGust", True),
        ):
            value, when = day_extreme(
                manager, table, day_start, dt, highest=highest, column=column
            )
            if value is not None:
                derived[key] = value
                derived[f"{key}Time"] = when
    except Exception:
        logger.error("Failed to compute DB-derived aggregates", exc_info=True)
    return derived
