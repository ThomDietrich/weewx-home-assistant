"""Utility functions and data."""

# Standard Python Libraries
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timezone
from enum import Enum
import logging
import re
from typing import Any

# Third-Party Libraries
import weewx  # type: ignore
import weewx.units  # type: ignore
from weewx.units import getStandardUnitType  # type: ignore

from .locale_loader import (
    load_enums,
    load_sensors,
    load_units,
    register_cache_invalidator,
)

logger = logging.getLogger(__name__)

# Caches over the locale data, each filled by its own getter on first use and
# dropped as a set by reset_caches below. Declaring them together is what keeps
# that pairing visible.
_ENUM_MAPS: dict[str, dict[int, str]] | None = None
_UNIT_METADATA: dict[str, dict[str, Any]] | None = None
_SENSORS_YAML: dict[str, Any] | None = None
_KEY_CONFIG: dict[str, Any] | None = None


def reset_caches() -> None:
    """Drop every cache built from the current locale state.

    The caches above are built once from the ``locale_loader`` state and never
    rebuilt, so changing the language or the config overrides afterwards has no
    effect on anything already loaded. ``locale_loader`` calls this when either
    changes, so a locale change is a locale change whenever it happens, instead
    of a silent no-op that depends on whether anyone has read a sensor yet.
    """
    global _ENUM_MAPS, _UNIT_METADATA, _SENSORS_YAML, _KEY_CONFIG
    _ENUM_MAPS = None
    _UNIT_METADATA = None
    _SENSORS_YAML = None
    _KEY_CONFIG = None


# locale_loader owns the language and the overrides and calls back when either
# is replaced. Registering, rather than being imported from there, is what makes
# the dependency one-way: this module imports locale_loader and locale_loader
# imports nothing back. A cache can only exist once this module has been
# imported, and importing it runs this line, so no cache can outlive its state.
register_cache_invalidator(reset_caches)


def get_enum_maps() -> dict[str, dict[int, str]]:
    """Get enum maps, loading them on first use."""
    global _ENUM_MAPS
    if _ENUM_MAPS is None:
        _ENUM_MAPS = load_enums()
    return _ENUM_MAPS


def _get_unit_metadata_dict() -> dict[str, dict[str, Any]]:
    """Get unit metadata, loading it on first use."""
    global _UNIT_METADATA
    if _UNIT_METADATA is None:
        _UNIT_METADATA = load_units()
    return _UNIT_METADATA


def get_sensors_yaml() -> dict[str, Any]:
    """Get the sensor catalogue, loading it on first use."""
    global _SENSORS_YAML
    if _SENSORS_YAML is None:
        _SENSORS_YAML = load_sensors()
    return _SENSORS_YAML


def degrees_to_cardinal(degrees: float) -> str:
    """Convert wind direction in degrees to cardinal direction.

    Parameters
    ----------
    degrees : float
        Wind direction in degrees (0-360)

    Returns
    -------
    str
        Cardinal direction (e.g., "N", "NE", "SW", "NNW")

    """
    # Each direction covers 22.5 degrees (360/16)
    # Add 11.25 to center the ranges, then divide by 22.5
    index = int((degrees + 11.25) / 22.5) % 16
    return get_enum_maps()["cardinal_directions"][index]


class UnitSystem(str, Enum):
    """Enumeration of unit systems supported by WeeWX.

    Casting to int returns the WeeWX unit system value.
    """

    # Declared so the value stashed in __dict__ below is a visible attribute
    # rather than one only __int__ knows about.
    _weewx_value: int

    METRIC = ("METRIC", weewx.METRIC)
    METRICWX = ("METRICWX", weewx.METRICWX)
    US = ("US", weewx.US)

    def __new__(cls, value: str, weewx_value: int) -> "UnitSystem":
        """Create a new instance of the enumeration."""
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.__dict__["_weewx_value"] = (
            weewx_value  # Using __dict__ to set a protected attribute
        )
        return obj

    def __int__(self) -> int:
        """Return the WeeWX unit system value."""
        return self._weewx_value

    def __str__(self) -> str:
        """Return the unit system value."""
        return self.value

    @classmethod
    def from_int(cls, value: int) -> "UnitSystem":
        """Get the unit system enumeration value from a WeeWX unit system value."""
        for unit_system in cls:
            if int(unit_system) == value:
                return unit_system
        raise ValueError(f"Invalid unit system value: {value}")


# Rain-group observations the catalogue publishes that WeeWX does not register:
# monthET and yearET come from the driver's LOOP packets (the Vantage reports
# both) and weekRain from a rain accumulator. WeeWX registers a unit group for
# none of them, so all three skipped conversion. The ET pair was additionally
# mislabelled, because the name-based guess in get_unit_metadata resolved "*ET"
# to the target system's unit -- a silent factor 25.4 on US hardware -- while
# weekRain reached Home Assistant with no unit at all. The registration lives
# beside get_unit_metadata because that is the lookup it makes correct; the
# aggregates this extension derives itself register in ``archive``.
# See felddy/weewx-home-assistant#62.
for _obs in ("monthET", "weekRain", "yearET"):
    weewx.units.obs_group_dict.setdefault(_obs, "group_rain")


def get_unit_metadata(measurement_name: str, unit_system: UnitSystem) -> dict[str, Any]:
    """Generate metadata for a measurement unit based on the unit system."""
    target_unit, _ = getStandardUnitType(int(unit_system), measurement_name)

    # Guessing a unit from the measurement name used to paper over observations
    # missing from obs_group_dict, which hid that their values were never
    # converted. Those are registered in controller.py now, and keys that carry
    # no unit at all declare "unitless" in the catalogue, so a miss here is a
    # genuine gap worth reporting.
    if target_unit is None:
        logger.warning(
            "No unit found for measurement '%s' in unit system %s",
            measurement_name,
            unit_system,
        )

    return _get_unit_metadata_dict().get(
        target_unit,
        {"unit_of_measurement": target_unit},  # Defaults to the WeeWX unit if not found
    )


# Abbreviated prefixes WeeWX uses, spelled out for the Home Assistant name.
# The first match wins, as only one of them can begin a key.
_PREFIX_EXPANSIONS = (
    ("In ", "Indoor "),
    ("Out ", "Outdoor "),
    ("Tx ", "Transmit "),
    ("Rx ", "Receive "),
)

# Keyword in the friendly name -> catalogue entry to borrow metadata from, for
# keys the catalogue does not list. The order is the precedence: first hit wins.
_GUESS_TEMPLATES = (
    ("alarm", "extraAlarm"),
    ("humidity", "outHumidity"),
    ("pressure", "pressure"),
    ("temperature", "outTemp"),
)


def _friendly_name(weewx_key: str) -> str:
    """Turn a WeeWX key into a readable sensor name.

    ``extraAlarm5`` becomes "Extra Alarm 5" and ``outTemp`` "Outdoor Temp".
    """
    # Space before digits (extraAlarm5 -> extraAlarm 5), then split camel case
    # (extraAlarm 5 -> Extra Alarm 5).
    spaced = re.sub(r"(\d+)", r" \1", weewx_key)
    name = re.sub(r"(?<!^)(?=[A-Z])", " ", spaced).title()
    for prefix, expansion in _PREFIX_EXPANSIONS:
        if name.startswith(prefix):
            return name.replace(prefix, expansion, 1)
    return name


def _guessed_key_config(name: str) -> dict[str, Any]:
    """Borrow a catalogue entry's metadata for a key that has none of its own."""
    key_config_dict = get_key_config_dict()
    lowered = name.lower()
    for keyword, template in _GUESS_TEMPLATES:
        if keyword in lowered:
            return deepcopy(key_config_dict[template])
    return {"metadata": {}}


def get_key_config(weewx_key: str) -> dict[str, Any]:
    """Return the catalogue entry for a WeeWX key, or the best guess at one.

    Three steps, in order: an exact catalogue match, the base entry of an
    indexed observation (``extraTemp3`` from ``extraTemp``), and failing both,
    a generated name with metadata borrowed from a similar sensor. The result
    is always a fresh dict the caller may mutate; the catalogue it comes from
    is shared and lazily cached.
    """
    key_config_dict = get_key_config_dict()

    config = key_config_dict.get(weewx_key)
    if config:
        return deepcopy(config)

    match = re.match(r"(.*?)(\d+)$", weewx_key)
    if match:
        base_key, suffix = match.groups()
        config = deepcopy(key_config_dict.get(base_key))
        if config:
            config["metadata"]["name"] = f"{config['metadata']['name']} {suffix}"
            return config

    name = _friendly_name(weewx_key)
    guess = _guessed_key_config(name)
    guess["metadata"]["name"] = name
    logger.warning("Guessed metadata for key '%s': %s", weewx_key, guess)
    return guess


def _beaufort_label(force: float) -> str:
    """Map a Beaufort force number to its label, clamped to the valid scale.

    The returned label is always a member of the ``beaufort_scale`` enum so the
    published value stays within the Home Assistant ``options`` list.
    """
    scale = get_enum_maps()["beaufort_scale"]
    keys = sorted(scale.keys())
    value = int(force)
    if value < keys[0] or value > keys[-1]:
        logger.warning(
            "Beaufort force %s outside valid range [%s, %s]; clamping",
            value,
            keys[0],
            keys[-1],
        )
        value = min(max(value, keys[0]), keys[-1])
    return scale[value]


# Converters referenced by name from the ``convert_lambda`` field in
# sensors.yaml. Every entry takes (value, config_publisher) so the registry has
# one call shape; most ignore the second argument. The names are part of the
# configuration surface -- users reference them from the ``sensors`` override --
# so they cannot be renamed without breaking existing configurations.
_LAMBDA_REGISTRY: dict[str, Callable[[Any, Any], Any]] = {
    "beaufort_scale_map": lambda x, cp: _beaufort_label(x),
    "degrees_to_cardinal": lambda x, cp: degrees_to_cardinal(x),
    # Identity, spelled as one. WeeWX timestamps are already Unix epochs, so
    # the datetime.fromtimestamp(x, utc).timestamp() this used to do returned
    # x and only looked like a time-zone conversion. The name stays: it is part
    # of the configuration surface, referenced by name from a sensors override.
    "localtime_to_utc_timestamp": lambda x, cp: x,
    "localtime_to_iso": lambda x, cp: datetime.fromtimestamp(
        x, tz=timezone.utc
    ).isoformat(),
    "unit_system_to_string": lambda x, cp: str(UnitSystem.from_int(x)),
}


def _build_key_config() -> dict[str, Any]:
    """Build KEY_CONFIG from YAML data with lambda functions.

    Returns
    -------
    dict[str, Any]
        Complete KEY_CONFIG dictionary with lambda functions

    """
    config = deepcopy(get_sensors_yaml())

    for sensor_name, sensor_config in config.items():
        # Replace convert_lambda string references with actual lambda functions
        if "convert_lambda" in sensor_config:
            lambda_name = sensor_config["convert_lambda"]
            if lambda_name in _LAMBDA_REGISTRY:
                sensor_config["convert_lambda"] = _LAMBDA_REGISTRY[lambda_name]
            else:
                logger.warning(
                    f"Unknown convert_lambda reference '{lambda_name}' for sensor '{sensor_name}'"
                )
                del sensor_config["convert_lambda"]

    return config


def get_key_config_dict() -> dict[str, Any]:
    """Get KEY_CONFIG dictionary (lazy loaded after language is set)."""
    global _KEY_CONFIG
    if _KEY_CONFIG is None:
        _KEY_CONFIG = _build_key_config()
    return _KEY_CONFIG
