"""Tests for the three ways get_key_config resolves a WeeWX key.

An exact catalogue match, the base entry of an indexed observation, and -- for
a key the catalogue does not list at all -- a generated name with metadata
borrowed from a similar sensor. The last path is the one that decides what an
unknown sensor looks like in Home Assistant, and it had no coverage.
"""

# Third-Party Libraries
import pytest

# Geekpad Libraries
from weewx_ha.utils import _friendly_name, get_key_config, get_key_config_dict


def test_an_exact_match_comes_from_the_catalogue():
    """A listed key is answered by its own entry."""
    catalogue = get_key_config_dict()
    assert (
        get_key_config("outTemp")["metadata"]["name"]
        == catalogue["outTemp"]["metadata"]["name"]
    )


def test_an_indexed_observation_borrows_its_base_entry():
    """extraTemp3 is not listed; extraTemp is, and lends its name and metadata."""
    config = get_key_config("extraTemp3")
    base = get_key_config_dict()["extraTemp"]
    assert config["metadata"]["name"] == f"{base['metadata']['name']} 3"
    assert config["metadata"].get("device_class") == base["metadata"].get(
        "device_class"
    )


def test_an_unknown_key_gets_a_generated_name_and_nothing_else():
    """With no catalogue entry and no keyword to go on, only a name is invented."""
    config = get_key_config("quuxLevel")
    assert config["metadata"]["name"] == "Quux Level"
    assert set(config["metadata"]) == {"name"}


def test_an_unknown_key_borrows_metadata_from_a_keyword_match():
    """A name containing 'temperature' is published as a temperature sensor."""
    config = get_key_config("weirdTemperature")
    assert config["metadata"]["name"] == "Weird Temperature"
    assert config["metadata"]["device_class"] == "temperature"


@pytest.mark.parametrize(
    ("key", "name"),
    [
        ("inWidget", "Indoor Widget"),
        ("outWidget", "Outdoor Widget"),
        ("txWidget", "Transmit Widget"),
        ("rxWidget", "Receive Widget"),
        ("extraAlarm5", "Extra Alarm 5"),
        ("outTemp", "Outdoor Temp"),
    ],
)
def test_friendly_names_expand_the_weewx_abbreviations(key, name):
    """The prefixes WeeWX abbreviates are spelled out, and digits are separated."""
    assert _friendly_name(key) == name


def test_the_catalogue_is_not_mutated_by_a_guess():
    """The guess borrows a deep copy, so the template keeps its own name."""
    get_key_config("weirdTemperature")
    assert get_key_config_dict()["outTemp"]["metadata"]["name"] != "Weird Temperature"
