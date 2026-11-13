"""Processes loop packets and publish state to MQTT."""

# Standard Python Libraries
import logging
from typing import Any

# Third-Party Libraries
import paho.mqtt.client as mqtt
from weewx.units import to_std_system

from .config_publisher import ConfigPublisher
from .utils import UnitSystem

logger = logging.getLogger(__name__)

# Packets to let past before a key with no configuration is worth a warning.
# Discovery registration and state publishing are ordered within a packet, so
# an unconfigured key means a genuine catalogue gap -- but only once the first
# packets of a run, which may still be settling, have gone by.
SETTLE_PACKET_COUNT = 5


class StatePublisher:
    """Process loop packets and publish state to MQTT."""

    def __init__(
        self,
        mqtt_client: mqtt.Client,
        config_publisher: ConfigPublisher,
        state_topic_prefix: str,
        unit_system: UnitSystem = UnitSystem.METRICWX,
    ):
        """Initialize the publisher.

        ``config_publisher`` is where the metadata for each key is looked up,
        so it has to be the same instance the discovery registration writes to.
        """
        self.mqtt_client = mqtt_client
        self.config_publisher = config_publisher
        self.state_topic_prefix = state_topic_prefix
        self.unit_system = unit_system
        self.settle_packets_remaining = SETTLE_PACKET_COUNT

    def process_packet(self, packet: dict[str, Any]) -> None:
        """Publish every configured value in the packet, and what derives from it."""
        if self.settle_packets_remaining > 0:
            self.settle_packets_remaining -= 1
        packet = to_std_system(packet, int(self.unit_system))
        for key, value in packet.items():
            if value is None:
                # Publishing None values causes Home Assistant templates to fail
                continue
            config = self.config_publisher.seen_measurements.get(key)
            if config is None:
                if self.settle_packets_remaining == 0:
                    logger.warning(f"Could not find configuration for key: {key}")
                continue

            # The derived sensors convert from the source value themselves, so
            # it has to be kept before the conversion below replaces it.
            original_value = value

            if convert_lambda := config.get("convert_lambda"):
                value = convert_lambda(value, self.config_publisher)
            # Retained + QoS 1: HA gets the latest value immediately on (re)connect
            # over a remote link; the LWT marks the device unavailable if it drops.
            self.mqtt_client.publish(
                f"{self.state_topic_prefix}/{key}", value, qos=1, retain=True
            )

            self._publish_derived_sensors(key, original_value)

    def _publish_derived_sensors(self, source_key: str, source_value: Any) -> None:
        """Publish the sensors that derive their value from ``source_key``."""
        derived_count = 0
        # Iterate a snapshot: ConfigPublisher.process_packet inserts from the
        # other worker thread, which would otherwise raise RuntimeError here.
        for sensor_name, sensor_config in list(
            self.config_publisher.seen_measurements.items()
        ):
            if sensor_config.get("source") == source_key:
                derived_count += 1
                if convert_lambda := sensor_config.get("convert_lambda"):
                    derived_value = convert_lambda(source_value, self.config_publisher)
                    self.mqtt_client.publish(
                        f"{self.state_topic_prefix}/{sensor_name}",
                        derived_value,
                        qos=1,
                        retain=True,
                    )
                    logger.debug(
                        f"Published derived sensor {sensor_name} from {source_key}: {derived_value}"
                    )
                else:
                    logger.warning(
                        f"Derived sensor {sensor_name} has source {source_key} but no convert_lambda"
                    )

        if derived_count > 0:
            logger.debug(f"Published {derived_count} derived sensors from {source_key}")
