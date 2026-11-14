"""Builds the configured, running paho MQTT client.

The order of the calls below is load-bearing in three places, and each reason
is a bug that was fixed once already, so this sits in its own module rather
than inside the WeeWX service class: the handler callbacks, the last will and
the broker address all have to be on the client before the network thread
starts.
"""

# Standard Python Libraries
import logging
from typing import Any, Protocol

# Third-Party Libraries
import paho.mqtt.client as mqtt

from .models import MQTTConfig

logger = logging.getLogger(__name__)


class MQTTHandler(Protocol):
    """The callbacks ``build_client`` wires into a paho client.

    Spelled out as a protocol because the wiring has to happen before
    ``loop_start``: the network thread can deliver a CONNACK the moment it
    exists, so a client handed back with unwired callbacks would drop it.
    """

    def on_mqtt_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        """Handle a connection attempt completing."""

    def on_mqtt_connect_fail(self, client: mqtt.Client, userdata: Any) -> None:
        """Handle a connection attempt failing outright."""

    def on_mqtt_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        disconnect_flags: mqtt.DisconnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        """Handle the connection to the broker going away."""

    def on_mqtt_message(
        self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage
    ) -> None:
        """Handle a PUBLISH message arriving from the broker."""

    def on_mqtt_subscribe(
        self,
        client: mqtt.Client,
        userdata: Any,
        mid: int,
        reason_code_list: list[mqtt.ReasonCode],
        properties: mqtt.Properties | None,
    ) -> None:
        """Handle the broker answering a subscribe request."""

    def on_mqtt_unsubscribe(
        self,
        client: mqtt.Client,
        userdata: Any,
        mid: int,
        reason_code_list: list[mqtt.ReasonCode],
        properties: mqtt.Properties | None,
    ) -> None:
        """Handle the broker answering an unsubscribe request."""


def build_client(
    mqtt_config: MQTTConfig, availability_topic: str, handler: MQTTHandler
) -> mqtt.Client:
    """Return a client wired to ``handler``, already connecting to the broker."""
    logger.debug(
        "MQTT configuration: host=%s port=%s tls=%s user=%s",
        mqtt_config.hostname,
        mqtt_config.port,
        mqtt_config.use_tls,
        "<set>" if mqtt_config.username else "<none>",
    )
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, mqtt_config.client_id)
    client.logger = logger
    client.on_connect = handler.on_mqtt_connect
    client.on_message = handler.on_mqtt_message
    client.on_subscribe = handler.on_mqtt_subscribe
    client.on_unsubscribe = handler.on_mqtt_unsubscribe
    client.on_disconnect = handler.on_mqtt_disconnect
    client.on_connect_fail = handler.on_mqtt_connect_fail
    if mqtt_config.use_tls:
        client.tls_set_context(mqtt_config.tls.context)
    # No guard: MQTTConfig requires a username of at least one character
    # and rejects an empty password, so there is no configuration that
    # reaches here without credentials.
    client.username_pw_set(
        mqtt_config.username, mqtt_config.password.get_secret_value()
    )
    # Set the last will and testament. The will travels inside the CONNECT
    # packet, so it has to be registered before the connection is made.
    client.will_set(availability_topic, "offline", qos=1, retain=True)
    # Hand the broker address to the client *before* starting the network
    # thread. loop_start() runs loop_forever(retry_first_connection=True),
    # which immediately calls reconnect() -- and reconnect() raises
    # ValueError("Invalid host.") while no host is set. loop_forever only
    # catches OSError, so that ValueError killed the network thread and the
    # client never connected or reconnected afterwards. connect_async()
    # records host/port/keep-alive and arms MQTT_CS_CONNECT_ASYNC, so the
    # thread performs the connect itself and retries with exponential
    # backoff for as long as the broker is unreachable (instead of raising
    # out of WeeWX service construction on the very first attempt).
    client.connect_async(mqtt_config.hostname, mqtt_config.port, mqtt_config.keep_alive)
    client.loop_start()
    return client
