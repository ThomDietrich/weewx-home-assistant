"""Regression tests for the WeeWX unit groups this extension registers.

An observation missing from ``weewx.units.obs_group_dict`` skips conversion in
``to_std_system`` altogether, yet ``get_unit_metadata`` still resolves a unit
for its discovery payload. The published value then carries the station's own
unit under the target system's label -- for the rain group that is a silent
factor 25.4 on US hardware.

See https://github.com/felddy/weewx-home-assistant/issues/62.
"""

# Standard Python Libraries
from unittest.mock import MagicMock, patch

# Third-Party Libraries
import pytest
import weewx
import weewx.units
from weewx.units import getStandardUnitType, to_std_system

# Geekpad Libraries
from weewx_ha import ConfigPublisher, UnitSystem
import weewx_ha.config_publisher as config_publisher
import weewx_ha.controller  # noqa: F401  (the import performs the registration)
from weewx_ha.locale_loader import set_config_overrides, set_language
from weewx_ha.models import StationInfo
from weewx_ha.utils import get_key_config_dict

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


# Base names the catalogue keeps for indexed observations. They never reach a
# packet bare -- get_key_config strips the numeric suffix off extraTemp1 and
# friends -- and WeeWX registers the indexed forms itself.
BASE_NAME_TEMPLATES = frozenset(
    {"extraHumid", "extraTemp", "leafTemp", "leafWet", "soilMoist", "soilTemp"}
)


@pytest.fixture
def reset_locale_state():
    """Reset global locale state before and after each test."""
    set_language(None)
    set_config_overrides(None)
    yield
    set_language(None)
    set_config_overrides(None)


def test_every_sensor_resolves_a_unit_or_declares_it_has_none(reset_locale_state):
    """Nothing may fall through get_unit_metadata unresolved any more.

    get_unit_metadata used to guess a unit from the measurement name, which
    hid observations missing from obs_group_dict. With the guessing gone, every
    catalogue sensor must either resolve a unit through WeeWX or say outright
    that it has none.
    """
    unresolved = []
    for key, config in sorted(get_key_config_dict().items()):
        if key in BASE_NAME_TEMPLATES:
            continue
        if config.get("integration", "sensor") != "sensor":
            continue
        if config.get("unitless"):
            continue
        if getStandardUnitType(METRICWX, key)[0] is None:
            unresolved.append(key)

    assert not unresolved, (
        "these keys resolve to no unit and do not declare 'unitless' in "
        f"sensors.yaml: {unresolved}"
    )


def _publisher():
    return ConfigPublisher(
        MagicMock(),
        "weather/status",
        "homeassistant",
        "weather",
        "weewx",
        StationInfo(name="Test Station", model="M", manufacturer="X", time_zone="UTC"),
        UnitSystem.METRICWX,
    )


def test_unitless_keys_skip_the_unit_lookup(reset_locale_state):
    """A unit-less key must not reach get_unit_metadata at all."""
    publisher = _publisher()
    with patch.object(config_publisher, "get_unit_metadata") as lookup:
        publisher.process_packet({"usUnits": weewx.US, "beaufort": 3})
    lookup.assert_not_called()
    assert publisher.seen_measurements["beaufort"]["metadata"]
    assert "unitless" not in publisher.seen_measurements["beaufort"]


def test_measured_keys_still_use_the_unit_lookup(reset_locale_state):
    """An ordinary measurement keeps resolving its unit through WeeWX."""
    publisher = _publisher()
    publisher.process_packet({"usUnits": weewx.US, "outTemp": 68.0})
    assert (
        publisher.seen_measurements["outTemp"]["metadata"]["unit_of_measurement"]
        == "°C"
    )
