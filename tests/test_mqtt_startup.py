"""Regression tests for MQTT client start-up ordering.

The network thread started by ``loop_start()`` runs
``loop_forever(retry_first_connection=True)``, which calls ``reconnect()``
straight away.  ``reconnect()`` raises ``ValueError("Invalid host.")`` while no
host has been recorded on the client, and ``loop_forever()`` only catches
``OSError`` -- so starting the loop before the host is known lets that
``ValueError`` escape and kill the thread.  A dead network thread never
connects and never reconnects, which silently takes the extension off the
broker until WeeWX is restarted.

See https://github.com/felddy/weewx-home-assistant/issues/74.
"""

# Standard Python Libraries
from unittest.mock import MagicMock, patch

# Third-Party Libraries
import paho.mqtt.client as mqtt
import pytest

# Geekpad Libraries
from weewx_ha.controller import Controller
from weewx_ha.models import MQTTConfig

HOSTNAME = "broker.example.test"
PORT = 1884
KEEP_ALIVE = 42


def _mqtt_config(**overrides) -> MQTTConfig:
    kwargs = {
        "hostname": HOSTNAME,
        "keep_alive": KEEP_ALIVE,
        "password": "secret",
        "port": PORT,
        "use_tls": False,
        "username": "weewx",
    }
    kwargs.update(overrides)
    return MQTTConfig(**kwargs)


@pytest.fixture
def mqtt_client():
    """Build an MQTT client through the controller, with paho mocked out."""
    controller = Controller.__new__(Controller)
    with patch.object(mqtt, "Client", return_value=MagicMock()) as client_class:
        client = Controller.init_mqtt_client(
            controller, _mqtt_config(), "weather/status"
        )
    assert client is client_class.return_value
    return client


def test_connect_async_is_called_before_loop_start(mqtt_client):
    """The host must be on the client before the network thread exists."""
    called = [call[0] for call in mqtt_client.mock_calls]
    assert "connect_async" in called
    assert "loop_start" in called
    assert called.index("connect_async") < called.index("loop_start")


def test_blocking_connect_is_not_used(mqtt_client):
    """A blocking ``connect()`` would raise out of WeeWX service construction."""
    mqtt_client.connect.assert_not_called()
    mqtt_client.connect_async.assert_called_once_with(HOSTNAME, PORT, KEEP_ALIVE)


def test_will_is_registered_before_connecting(mqtt_client):
    """The LWT only reaches the broker if it is set before the CONNECT packet."""
    called = [call[0] for call in mqtt_client.mock_calls]
    assert called.index("will_set") < called.index("connect_async")
    mqtt_client.will_set.assert_called_once_with(
        "weather/status", "offline", qos=1, retain=True
    )


def test_connect_failures_are_reported(mqtt_client):
    """With retrying start-up connects, failures must be visible in the log."""
    assert mqtt_client.on_connect_fail.__func__ is Controller.on_mqtt_connect_fail


def test_paho_rejects_reconnect_without_a_host():
    """Document the paho behaviour that makes the ordering mandatory."""
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    with pytest.raises(ValueError, match="Invalid host."):
        client.reconnect()
    client.connect_async(HOSTNAME, PORT, KEEP_ALIVE)
    assert client.host == HOSTNAME
    assert client.port == PORT
    assert client.keepalive == KEEP_ALIVE
