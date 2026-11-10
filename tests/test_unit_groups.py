"""Regression tests for the WeeWX unit groups this extension registers.

An observation missing from ``weewx.units.obs_group_dict`` skips conversion in
``to_std_system`` altogether, yet ``get_unit_metadata`` still resolves a unit
for its discovery payload. The published value then carries the station's own
unit under the target system's label -- for the rain group that is a silent
factor 25.4 on US hardware.

See https://github.com/felddy/weewx-home-assistant/issues/62.
"""

# Third-Party Libraries
import pytest
import weewx
import weewx.units
from weewx.units import to_std_system

# Geekpad Libraries
from weewx_ha import UnitSystem
import weewx_ha.controller  # noqa: F401  (the import performs the registration)

METRICWX = int(UnitSystem.METRICWX)
INCH_TO_MM = 25.4

# Every rain-group observation this extension publishes that WeeWX itself does
# not register: the DB-derived aggregates, the station's cumulative ET totals
# and the weekly rain accumulator.
RAIN_GROUP_KEYS = (
    "dayET",
    "eventRain",
    "hourRain",
    "monthET",
    "rain24",
    "weekRain",
    "yearET",
)


@pytest.mark.parametrize("key", RAIN_GROUP_KEYS)
def test_rain_group_observation_is_registered(key):
    """Each rain-group observation we publish carries a unit group."""
    assert weewx.units.obs_group_dict.get(key) == "group_rain"


@pytest.mark.parametrize("key", RAIN_GROUP_KEYS)
def test_rain_group_observation_converts_from_us_units(key):
    """An inch reported by US hardware reaches METRICWX as 25.4 mm."""
    converted = to_std_system({"usUnits": weewx.US, key: 1.0}, METRICWX)
    assert converted[key] == pytest.approx(INCH_TO_MM)
