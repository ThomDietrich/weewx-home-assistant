"""Tests for the configuration models' validation behaviour."""

# Standard Python Libraries
from zoneinfo import ZoneInfo

# Third-Party Libraries
from pydantic import ValidationError
import pytest

# Geekpad Libraries
from weewx_ha.models import StationInfo

STATION = {"name": "Test Station", "model": "M", "manufacturer": "X"}


def test_a_zone_name_becomes_a_zoneinfo():
    """The configuration carries a name; the model carries the zone."""
    station = StationInfo(**STATION, timezone="Europe/Berlin")
    assert station.time_zone == ZoneInfo("Europe/Berlin")


def test_a_zoneinfo_is_accepted_unchanged():
    """Constructing the model from an already-built zone must not re-resolve it."""
    zone = ZoneInfo("UTC")
    assert StationInfo(**STATION, timezone=zone).time_zone is zone


def test_the_time_zone_defaults_to_utc():
    """The field is unused, so configurations that omit it still validate."""
    assert StationInfo(**STATION).time_zone == ZoneInfo("UTC")


@pytest.mark.parametrize("bad", ["Not/AZone", 123, ["Europe/Berlin"]])
def test_an_unusable_time_zone_is_a_validation_error(bad):
    """A name that is not a zone and a value that is not a name both get rejected.

    Only ZoneInfoNotFoundError used to be caught, so a value of the wrong type
    raised TypeError straight out of the validator instead of arriving as a
    validation error naming the offending input.
    """
    with pytest.raises(ValidationError) as caught:
        StationInfo(**STATION, timezone=bad)
    assert "Invalid time zone" in str(caught.value)
