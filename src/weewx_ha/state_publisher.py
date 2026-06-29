"""Processes loop packets and publish state to MQTT."""

# Standard Python Libraries
import logging

# Third-Party Libraries
import paho.mqtt.client as mqtt
from weewx.units import to_std_system

from . import ConfigPublisher, UnitSystem

logger = logging.getLogger(__name__)

# Constants
# The number of loop packets to wait before emitting warnings for missing configurations
SETTLE_COUNTDOWN_START = 5


class StatePublisher:
    """Process loop packets and publish state to MQTT.

    Attributes
    ----------
    mqtt_client : mqtt.Client
        The MQTT client used for publishing state updates.
    config_publisher : ConfigPublisher
        The configuration publisher used for looking up measurement metadata.
    state_topic_prefix : str
        The prefix for the MQTT topic where state updates will be published.
    unit_system : UnitSystem
        The unit system to use for the state updates.
    """

    def __init__(
        self,
        mqtt_client: mqtt.Client,
        config_publisher: ConfigPublisher,
        state_topic_prefix: str,
        unit_system: UnitSystem = UnitSystem.METRICWX,
    ):
        """
        Initialize the publisher.

        Parameters
        ----------
        mqtt_client : mqtt.Client
            The MQTT client used for publishing state updates.
        config_publisher : ConfigPublisher
            The configuration publisher used for looking up measurement metadata.
        state_topic_prefix : str
            The prefix for the MQTT topic where state updates will be published.
        unit_system : UnitSystem, optional
            The unit system to use for the state updates. Default is UnitSystem.METRICWX.
        """
        self.mqtt_client = mqtt_client
        self.config_publisher = config_publisher
        self.state_topic_prefix = state_topic_prefix
        self.unit_system = unit_system
        self.settled_countdown = SETTLE_COUNTDOWN_START

    def process_packet(self, packet: dict) -> None:
        """Process record and publish to MQTT."""
        logger.debug("Processing packet")
        if self.settled_countdown > 0:
            # Wait for the system to settle before emitting warnings for missing configurations
            self.settled_countdown -= 1
        if self.unit_system is not None:
            packet = to_std_system(packet, int(self.unit_system))
        for key, value in packet.items():
            if value is None:
                # Publishing None values causes Home Assistant templates to fail
                continue
            config = self.config_publisher.seen_measurements.get(key)
            if config is None:
                # Skip if the configuration is not found in the seen measurements
                if self.settled_countdown == 0:
                    # Emit warnings for missing configurations after the system has settled
                    logger.warning(f"Could not find configuration for key: {key}")
                continue

            # Store original value for derived sensors before conversion
            original_value = value

            if convert_lambda := config.get("convert_lambda"):
                # Apply conversion lambda if it exists
                value = convert_lambda(value, self.config_publisher)
            # Retained + QoS 1: HA gets the latest value immediately on (re)connect
            # over a remote link; the LWT marks the device unavailable if it drops.
            self.mqtt_client.publish(
                f"{self.state_topic_prefix}/{key}", value, qos=1, retain=True
            )

            # Publish derived sensors that use this key as source (use original value)
            self._publish_derived_sensors(key, original_value)

    def _publish_derived_sensors(self, source_key: str, source_value: float) -> None:
        """Publish derived sensors that use the source key as their data source.

        Parameters
        ----------
        source_key : str
            The WeeWX key that is used as a source
        source_value : float
            The value from the source key

        Returns
        -------
        None
        """
        # Find all sensors in seen_measurements that have this source_key
        # Create a snapshot of items to avoid RuntimeError if dictionary changes during iteration
        derived_count = 0
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
