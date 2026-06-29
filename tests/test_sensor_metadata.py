"""Validate the sensor catalogue: unique names and valid HA metadata.

These guard the naming/structure normalization against regressions: duplicate
display names (which collide under the device-name prefix in Home Assistant) and
device_class/state_class/entity_category values that HA would reject (silently
dropping the entity).
"""

# Third-Party Libraries
import pytest

# Geekpad Libraries
from weewx_ha.locale_loader import load_sensors, set_config_overrides, set_language

# Home Assistant sensor + binary_sensor device classes (current as of 2026.6).
VALID_DEVICE_CLASSES = {
    "absolute_humidity", "apparent_power", "aqi", "atmospheric_pressure",
    "battery", "carbon_dioxide", "carbon_monoxide", "cold", "conductivity",
    "connectivity", "current", "data_rate", "data_size", "date", "distance",
    "door", "duration", "energy", "energy_storage", "enum", "frequency", "gas",
    "garage_door", "heat", "humidity", "illuminance", "irradiance", "lock",
    "moisture", "monetary", "moving", "nitrogen_dioxide", "nitrogen_monoxide",
    "nitrous_oxide", "occupancy", "opening", "ozone", "ph", "pm1", "pm10",
    "pm25", "power", "power_factor", "precipitation", "precipitation_intensity",
    "presence", "pressure", "problem", "reactive_power", "running", "safety",
    "signal_strength", "smoke", "sound", "sound_pressure", "speed",
    "sulphur_dioxide", "tamper", "temperature", "timestamp", "update",
    "vibration", "volatile_organic_compounds",
    "volatile_organic_compounds_parts", "voltage", "volume", "volume_flow_rate",
    "volume_storage", "water", "weight", "wind_direction", "wind_speed",
    "window",
}
VALID_STATE_CLASSES = {
    "measurement", "measurement_angle", "total", "total_increasing",
}
VALID_ENTITY_CATEGORIES = {"diagnostic", "config"}


@pytest.fixture
def sensors():
    """Load the resolved sensor catalogue with default (English) locale."""
    set_language(None)
    set_config_overrides(None)
    return load_sensors()


def _metadata(cfg):
    return (cfg or {}).get("metadata", {})


def test_no_duplicate_enabled_names(sensors):
    """No two enabled sensors may share a display name (HA prefixes the device
    name, so duplicates produce indistinguishable entities)."""
    seen: dict[str, str] = {}
    dups: dict[str, list[str]] = {}
    for key, cfg in sensors.items():
        md = _metadata(cfg)
        if not md.get("enabled_by_default", True):
            continue
        name = md.get("name")
        if name in seen:
            dups.setdefault(name, [seen[name]]).append(key)
        else:
            seen[name] = key
    assert not dups, f"Duplicate enabled sensor names: {dups}"


def test_device_classes_valid(sensors):
    bad = {
        key: _metadata(cfg).get("device_class")
        for key, cfg in sensors.items()
        if _metadata(cfg).get("device_class") is not None
        and _metadata(cfg).get("device_class") not in VALID_DEVICE_CLASSES
    }
    assert not bad, f"Unknown device_class values: {bad}"


def test_state_classes_valid(sensors):
    bad = {
        key: _metadata(cfg).get("state_class")
        for key, cfg in sensors.items()
        if _metadata(cfg).get("state_class") is not None
        and _metadata(cfg).get("state_class") not in VALID_STATE_CLASSES
    }
    assert not bad, f"Unknown state_class values: {bad}"


def test_entity_categories_valid(sensors):
    bad = {
        key: _metadata(cfg).get("entity_category")
        for key, cfg in sensors.items()
        if _metadata(cfg).get("entity_category") is not None
        and _metadata(cfg).get("entity_category") not in VALID_ENTITY_CATEGORIES
    }
    assert not bad, f"Unknown entity_category values: {bad}"
