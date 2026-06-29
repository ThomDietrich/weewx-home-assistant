"""Tests for the MQTT publish layer: retain/QoS and raw-value behaviour."""

# Standard Python Libraries
import json
from unittest.mock import MagicMock

# Third-Party Libraries
import pytest

# Geekpad Libraries
from weewx_ha import ConfigPublisher, StatePublisher, UnitSystem
from weewx_ha.locale_loader import set_config_overrides, set_language
from weewx_ha.models import StationInfo

METRICWX = int(UnitSystem.METRICWX)


@pytest.fixture
def reset_locale_state():
    """Reset global locale state before and after each test."""
    set_language(None)
    set_config_overrides(None)
    yield
    set_language(None)
    set_config_overrides(None)


def _station():
    return StationInfo(
        name="Test Station", model="M", manufacturer="X", time_zone="UTC"
    )


def _config_publisher(client):
    return ConfigPublisher(
        client,
        "weather/status",
        "homeassistant",
        "weather",
        "weewx",
        _station(),
        UnitSystem.METRICWX,
    )


def test_discovery_is_retained_qos1_with_precision(reset_locale_state):
    """Discovery is retained at QoS 1 and carries suggested_display_precision
    (and no rounding value_template)."""
    client = MagicMock()
    cp = _config_publisher(client)
    cp.process_packet({"usUnits": METRICWX, "outTemp": 21.347})
    cp.publish_discovery()

    calls = {c.args[0]: c for c in client.publish.call_args_list}
    topic = "homeassistant/sensor/weewx/outTemp/config"
    assert topic in calls, f"missing discovery for outTemp; got {list(calls)}"
    call = calls[topic]
    assert call.kwargs.get("retain") is True
    assert call.kwargs.get("qos") == 1
    payload = json.loads(call.args[1])
    assert payload["suggested_display_precision"] == 1
    assert "value_template" not in payload
    assert payload["unique_id"] == "weewx_outTemp"
    assert payload["origin"]["name"] == "weewx-home-assistant"


def test_state_is_retained_qos1_and_raw(reset_locale_state):
    """State is retained at QoS 1 and the raw, unrounded value reaches the topic."""
    client = MagicMock()
    cp = _config_publisher(client)
    cp.process_packet({"usUnits": METRICWX, "outTemp": 21.347})
    sp = StatePublisher(client, cp, "weather", UnitSystem.METRICWX)

    client.reset_mock()
    raw = 21.3477777
    sp.process_packet({"usUnits": METRICWX, "outTemp": raw})

    calls = {c.args[0]: c for c in client.publish.call_args_list}
    assert "weather/outTemp" in calls, f"missing state for outTemp; got {list(calls)}"
    call = calls["weather/outTemp"]
    assert call.kwargs.get("retain") is True
    assert call.kwargs.get("qos") == 1
    # Raw, unrounded value reaches the state topic (HA rounds for display only).
    assert call.args[1] == raw
