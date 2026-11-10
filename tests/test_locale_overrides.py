"""Test locale configuration overrides functionality."""

# Standard Python Libraries
from typing import Any

# Geekpad Libraries
from weewx_ha.locale_loader import (
    get_config_overrides,
    load_enums,
    load_sensors,
    load_units,
    set_config_overrides,
    set_language,
)


def test_config_overrides_sensors():
    """Test that sensor configuration can be overridden from config."""
    # Set up overrides
    overrides = {
        "sensors": {
            "outTemp": {
                "metadata": {
                    "name": "Custom Outdoor Temperature",
                }
            }
        }
    }
    set_config_overrides(overrides)

    # Load sensors
    sensors = load_sensors()

    # Verify override was applied
    assert sensors["outTemp"]["metadata"]["name"] == "Custom Outdoor Temperature"
    # Verify other properties are preserved from YAML
    assert "device_class" in sensors["outTemp"]["metadata"]


def test_config_overrides_units():
    """Test that unit metadata can be overridden from config."""
    # Set up overrides
    overrides = {
        "units": {
            "degree_C": {
                "value_template": "{{ value | round(0) }}",
            }
        }
    }
    set_config_overrides(overrides)

    # Load units
    units = load_units()

    # Verify override was applied
    assert units["degree_C"]["value_template"] == "{{ value | round(0) }}"
    # Verify unit_of_measurement is preserved from YAML
    assert units["degree_C"]["unit_of_measurement"] == "°C"


def test_config_overrides_enums():
    """Test that enum mappings can be overridden from config."""
    # Set up overrides
    overrides = {
        "enums": {
            "cardinal_directions": {
                0: "Custom North",
                1: "Custom NNE",
            }
        }
    }
    set_config_overrides(overrides)

    # Load enums
    enums = load_enums()

    # Verify overrides were applied
    assert enums["cardinal_directions"][0] == "Custom North"
    assert enums["cardinal_directions"][1] == "Custom NNE"
    # Verify other directions are preserved from YAML
    assert 2 in enums["cardinal_directions"]


def test_config_overrides_priority_over_localized():
    """Test that config overrides have priority over localized YAML."""
    # Set language to Czech
    set_language("cs")

    # Set up overrides that should override Czech localization
    overrides = {
        "sensors": {
            "outTemp": {
                "metadata": {
                    "name": "Config Override Name",
                }
            }
        }
    }
    set_config_overrides(overrides)

    # Load sensors
    sensors = load_sensors()

    # Verify config override has highest priority
    assert sensors["outTemp"]["metadata"]["name"] == "Config Override Name"


def test_partial_override():
    """Test that partial overrides only change specified fields."""
    # Load original data
    set_config_overrides(None)
    original_sensors = load_sensors()
    original_outtemp = original_sensors["outTemp"]["metadata"].copy()

    # Set up partial override (only name)
    overrides = {
        "sensors": {
            "outTemp": {
                "metadata": {
                    "name": "Partial Override",
                }
            }
        }
    }
    set_config_overrides(overrides)

    # Load sensors with override
    sensors = load_sensors()

    # Verify only name was overridden
    assert sensors["outTemp"]["metadata"]["name"] == "Partial Override"
    # Verify other fields are unchanged
    assert (
        sensors["outTemp"]["metadata"]["device_class"]
        == original_outtemp["device_class"]
    )
    assert (
        sensors["outTemp"]["metadata"]["state_class"] == original_outtemp["state_class"]
    )


def test_multiple_override_types():
    """Test that multiple override types (sensors, units, enums) work together."""
    # Set up overrides for all types
    overrides = {
        "sensors": {
            "outTemp": {
                "metadata": {
                    "name": "Custom Temp",
                }
            }
        },
        "units": {
            "degree_C": {"value_template": "{{ value | round(0) }}"},
        },
        "enums": {
            "cardinal_directions": {0: "Custom N"},
        },
    }
    set_config_overrides(overrides)

    # Load all types
    sensors = load_sensors()
    units = load_units()
    enums = load_enums()

    # Verify all overrides were applied
    assert sensors["outTemp"]["metadata"]["name"] == "Custom Temp"
    assert units["degree_C"]["value_template"] == "{{ value | round(0) }}"
    assert enums["cardinal_directions"][0] == "Custom N"


def test_get_set_config_overrides():
    """Test getter and setter for config overrides."""
    # Initially should be None
    assert get_config_overrides() is None

    # Set overrides
    overrides: dict[str, Any] = {"sensors": {"test": "value"}}
    set_config_overrides(overrides)

    # Verify getter returns what was set
    assert get_config_overrides() == overrides

    # Clear overrides
    set_config_overrides(None)
    assert get_config_overrides() is None


def test_no_overrides():
    """Test that loading works normally when no overrides are set."""
    set_config_overrides(None)

    # Load data
    sensors = load_sensors()
    units = load_units()
    enums = load_enums()

    # Verify data was loaded (basic sanity check)
    assert "outTemp" in sensors
    assert "degree_C" in units
    assert "cardinal_directions" in enums
