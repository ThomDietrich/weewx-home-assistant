"""Regression tests for the loop-packet pre-processor.

The Davis Vantage reports transmitter battery health as a bitmap in
``txBatteryStatus``, one bit per transmitter. The pre-processor fans those bits
out into the eight ``batteryStatus*`` fields the sensor catalogue declares as
binary sensors -- and the catalogue matches them against ``payload_on: 1``.

Publishing the bit's *weight* instead of the bit itself therefore leaves every
field above bit 0 permanently off: ``batteryStatusChannel1`` reports 2,
``batteryStatusChannel7`` reports 128, and neither ever equals the payload Home
Assistant is told to look for. Seven of the eight battery sensors are mute.

See https://github.com/felddy/weewx-home-assistant/issues/62 for the catalogue
contract these tests pin.
"""

# Standard Python Libraries
from itertools import product

# Third-Party Libraries
import pytest

# Geekpad Libraries
from weewx_ha.preprocessor import TX_BATTERY_STATUS_FIELDS, PacketPreprocessor
from weewx_ha.utils import get_key_config

ALL_BITMAPS = range(256)


def _payloads(field):
    """Return the (off, on) payloads the catalogue declares for a field."""
    metadata = get_key_config(field)["metadata"]
    return metadata["payload_off"], metadata["payload_on"]


def _process(value):
    return PacketPreprocessor().process_packet({"txBatteryStatus": value})


def test_every_battery_field_uses_a_payload_the_catalogue_declares():
    """Published values must be the payloads Home Assistant is told to match.

    This is the defect: the fan-out publishes the bit's weight, so every field
    above bit 0 carries a value that matches neither payload_on nor payload_off.
    """
    offenders = []
    for bitmap, (index, field) in product(
        ALL_BITMAPS, enumerate(TX_BATTERY_STATUS_FIELDS)
    ):
        value = _process(bitmap)[field]
        if value not in _payloads(field):
            offenders.append((bitmap, field, value))
    assert not offenders, (
        "these (bitmap, field, published value) triples are neither payload_on "
        f"nor payload_off: {offenders[:12]} ... {len(offenders)} in total"
    )
    assert index == len(TX_BATTERY_STATUS_FIELDS) - 1  # the loop covered them all


def test_all_transmitters_report_low_when_every_bit_is_set():
    """A bitmap of 255 means all eight transmitters are low, not just the ISS."""
    packet = _process(255)
    for field in TX_BATTERY_STATUS_FIELDS:
        _, on = _payloads(field)
        assert packet[field] == on, f"{field} does not report low for bitmap 255"


@pytest.mark.parametrize("index", range(len(TX_BATTERY_STATUS_FIELDS)))
def test_one_bit_marks_exactly_one_transmitter(index):
    """Bit n flags transmitter n and leaves the other seven alone."""
    packet = _process(1 << index)
    for other, field in enumerate(TX_BATTERY_STATUS_FIELDS):
        off, on = _payloads(field)
        assert packet[field] == (
            on if other == index else off
        ), f"bitmap {1 << index} reported {field} as {packet[field]}"


def test_a_missing_reading_neither_raises_nor_invents_a_state():
    """A None bitmap must not take the whole packet down with it.

    ``process_packet`` runs on a worker thread and ``preprocessor_complete``
    answers an exception by logging and returning, so a TypeError here discards
    every measurement in the packet, not just the battery fields. And since a
    missing reading says nothing about the transmitters, no battery field may be
    invented for it -- reporting them all healthy would be a guess.
    """
    packet = PacketPreprocessor().process_packet(
        {"txBatteryStatus": None, "outTemp": 21.3}
    )
    assert packet["outTemp"] == 21.3
    assert not any(field in packet for field in TX_BATTERY_STATUS_FIELDS)


@pytest.mark.parametrize("value", [0, 7, 255, None])
def test_the_bitmap_key_never_survives(value):
    """``txBatteryStatus`` is consumed here; it is not a sensor of its own."""
    assert "txBatteryStatus" not in _process(value)


def test_packets_without_a_bitmap_pass_through_untouched():
    """Stations that report no battery status are left alone."""
    packet = {"outTemp": 21.3, "usUnits": 17}
    assert PacketPreprocessor().process_packet(dict(packet)) == packet
