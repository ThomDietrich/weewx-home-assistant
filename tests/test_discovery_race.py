"""Regression tests for the discovery publishing race in ``ConfigPublisher``.

``Controller`` drives a two-worker thread pool, so
``ConfigPublisher.process_packet`` (which inserts into ``seen_measurements``)
and ``ConfigPublisher.publish_discovery`` (which walks it) can overlap:
``publish_discovery`` is submitted from the MQTT connect handler, from the Home
Assistant birth-message handler and from ``check_config_update``, while the
next LOOP packet is still being processed. Iterating the live dictionary raised
``RuntimeError: dictionary changed size during iteration``, and building an
entry in place let a reader see a configuration without its metadata.

See https://github.com/felddy/weewx-home-assistant/issues/42 and
https://github.com/felddy/weewx-home-assistant/issues/45.
"""

# Standard Python Libraries
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# Third-Party Libraries
import paho.mqtt.client as mqtt
import pytest

# Geekpad Libraries
from weewx_ha import ConfigPublisher, UnitSystem
import weewx_ha.config_publisher as config_publisher
from weewx_ha.models import StationInfo

METRICWX = int(UnitSystem.METRICWX)


@pytest.fixture
def publisher():
    """Build a ConfigPublisher wired to a mock MQTT client."""
    return ConfigPublisher(
        MagicMock(),
        "weather/status",
        "homeassistant",
        "weather",
        "weewx",
        StationInfo(name="Test Station", model="M", manufacturer="X", time_zone="UTC"),
        UnitSystem.METRICWX,
    )


def test_publish_survives_insert_during_iteration(publisher):
    """A packet processed mid-publish must not abort the discovery run."""
    publisher.process_packet({"usUnits": METRICWX, "outTemp": 21.3})

    # Stand in for the other worker thread: the first discovery publish causes
    # a new measurement to be registered while the loop is still running.
    def publish_then_discover(*args, **kwargs):
        if "inTemp" not in publisher.seen_measurements:
            publisher.process_packet({"usUnits": METRICWX, "inTemp": 19.8})
        return MagicMock()

    publisher.mqtt_client.publish.side_effect = publish_then_discover

    publisher.publish_discovery()

    assert publisher.mqtt_client.publish.call_count >= 1
    assert "inTemp" in publisher.seen_measurements


def test_entries_are_never_visible_without_metadata(publisher):
    """An entry becomes visible only once its metadata is attached."""
    observed = []
    real_get_unit_metadata = config_publisher.get_unit_metadata

    def spy(measurement_name, unit_system):
        # Mid-build: the key must not be readable yet, or a concurrent
        # publish_discovery would emit a payload with no metadata.
        observed.append(measurement_name in publisher.seen_measurements)
        return real_get_unit_metadata(measurement_name, unit_system)

    with patch.object(config_publisher, "get_unit_metadata", side_effect=spy):
        publisher.process_packet({"usUnits": METRICWX, "outTemp": 21.3})

    assert observed, "get_unit_metadata was never consulted"
    assert not any(observed), "a key was readable before its metadata existed"
    assert publisher.seen_measurements["outTemp"]["metadata"]


def test_every_seen_measurement_carries_metadata(publisher):
    """Both discovery paths -- direct and derived -- populate metadata."""
    # windDir brings the derived windDirCardinal sensor along with it.
    publisher.process_packet({"usUnits": METRICWX, "windDir": 180.0})

    assert "windDirCardinal" in publisher.seen_measurements, "no derived sensor found"
    for name, config in publisher.seen_measurements.items():
        assert "metadata" in config, f"{name} has no metadata"


def test_unknown_key_lookup_does_not_create_an_entry(publisher):
    """seen_measurements must not be a defaultdict: reads cannot insert."""
    publisher.process_packet({"usUnits": METRICWX, "outTemp": 21.3})
    before = set(publisher.seen_measurements)

    with pytest.raises(KeyError):
        publisher.seen_measurements["noSuchMeasurement"]

    assert set(publisher.seen_measurements) == before


def test_a_rejected_discovery_publish_is_reported(publisher, caplog):
    """A discovery configuration goes out once; losing it silently is a lost entity.

    State values repeat with every packet, so a dropped one corrects itself.
    A discovery payload is published once per newly seen key, and nothing used
    to look at the return code -- a rejected publish left a sensor that simply
    never appeared, with nothing in the log to explain it.
    """
    publisher.process_packet({"usUnits": METRICWX, "outTemp": 21.3})
    publisher.mqtt_client.publish.return_value = SimpleNamespace(
        rc=mqtt.MQTT_ERR_QUEUE_SIZE
    )

    with caplog.at_level(logging.WARNING, logger="weewx_ha"):
        publisher.publish_discovery()

    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("outTemp" in message for message in warnings), warnings


def test_a_queued_discovery_publish_is_not_reported_as_lost(publisher, caplog):
    """A publish made while the broker is away is queued, not dropped.

    For QoS 1 paho keeps the message in its outgoing queue and sends it on
    reconnect, so MQTT_ERR_NO_CONN is not a lost entity. Warning about it
    would report every entity discovered during an outage as missing from
    Home Assistant while all of them are still on their way.
    """
    publisher.process_packet({"usUnits": METRICWX, "outTemp": 21.3})
    publisher.mqtt_client.publish.return_value = SimpleNamespace(
        rc=mqtt.MQTT_ERR_NO_CONN
    )

    with caplog.at_level(logging.DEBUG, logger="weewx_ha"):
        publisher.publish_discovery()

    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings == [], warnings
    assert any(
        "outTemp" in r.getMessage() and "reconnect" in r.getMessage()
        for r in caplog.records
    ), [r.getMessage() for r in caplog.records]
