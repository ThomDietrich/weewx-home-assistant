"""Processes loop packets and publish MQTT discovery configurations."""

# Standard Python Libraries
from collections import defaultdict
import json
import logging
from typing import Any
from zoneinfo import ZoneInfo

# Third-Party Libraries
import paho.mqtt.client as mqtt
from weewx.units import to_std_system  # type: ignore

from ._version import __version__
from .models import StationInfo
from .utils import UnitSystem, get_key_config, get_unit_metadata

logger = logging.getLogger(__name__)


class ConfigPublisher:
    """Process loop packets and publish MQTT discovery configurations.

    Attributes
    ----------
    availability_topic : str
        The MQTT topic for availability status.
    discovery_topic_prefix : str
        The prefix for MQTT discovery topics.
    mqtt_client : mqtt.Client
        The MQTT client used for publishing messages.
    node_id : str
        The unique identifier for the node.
    state_topic_prefix : str
        The prefix for MQTT state topics.
    unit_system : UnitSystem
        The unit system to use for measurements.
    seen_measurements : dict of str to dict of str to Any
        A dictionary to hold measurement metadata.
    """

    def __init__(
        self,
        mqtt_client: mqtt.Client,
        availability_topic: str,
        discovery_topic_prefix: str,
        state_topic_prefix: str,
        node_id: str,
        station_info: StationInfo,
        unit_system: UnitSystem = UnitSystem.METRICWX,
    ):
        """
        Initialize the ConfigPublisher.

        Parameters
        ----------
        mqtt_client : mqtt.Client
            The MQTT client used for publishing messages.
        availability_topic : str
            The MQTT topic for availability status.
        discovery_topic_prefix : str
            The prefix for MQTT discovery topics.
        state_topic_prefix : str
            The prefix for MQTT state topics.
        node_id : str
            The unique identifier for the node.
        station_info : StationInfo
            The weather station information.
        unit_system : UnitSystem, optional
            The unit system to use for measurements (default is UnitSystem.METRICWX).
        """
        self.availability_topic: str = availability_topic
        self.discovery_topic_prefix: str = discovery_topic_prefix
        self.mqtt_client: mqtt.Client = mqtt_client
        self.node_id: str = node_id
        self.state_topic_prefix: str = state_topic_prefix
        self.unit_system: UnitSystem = unit_system

        # Device metadata to include in discovery configurations
        self.device_description: dict[str, Any] = {
            "device": {
                "identifiers": [self.node_id],
                "name": station_info.name,
                "model": station_info.model,
                "manufacturer": station_info.manufacturer,
            }
        }

        # Origin metadata identifies the publishing software in Home Assistant's
        # discovery logs (recommended best practice; required for device-based
        # discovery).
        self.origin_description: dict[str, Any] = {
            "origin": {
                "name": "weewx-home-assistant",
                "sw_version": __version__,
                "support_url": "https://github.com/ThomDietrich/weewx-home-assistant",
            }
        }

        self.time_zone: ZoneInfo = station_info.time_zone

        # Dictionary to hold measurement configuration and metadata
        self.seen_measurements: dict[str, dict[str, Any]] = defaultdict(dict)

    def process_packet(self, packet: dict) -> bool:
        """
        Process packet and compose discovery configurations.

        Parameters
        ----------
        packet : dict
            The packet of measurements to process.

        Returns
        -------
        bool
            True if new measurements were discovered, False otherwise.

        """
        logger.debug("Processing packet")
        found_new_measurements = False
        if self.unit_system is not None:
            packet = to_std_system(packet, int(self.unit_system))
        for key in packet.keys():
            if key not in self.seen_measurements:
                logger.debug(f"Discovered new measurement: {key}")
                found_new_measurements = True
                self.seen_measurements[key] |= get_key_config(key)
                # Unit metadata is underlaid with the metadata from the key
                self.seen_measurements[key]["metadata"] = get_unit_metadata(
                    key, self.unit_system
                ) | self.seen_measurements[key].get("metadata", {})

                # Check if there are any derived sensors that use this key as source
                if self._discover_derived_sensors(key):
                    found_new_measurements = True

        return found_new_measurements

    def _discover_derived_sensors(self, source_key: str) -> bool:
        """Discover and register sensors that derive from a source sensor.

        Parameters
        ----------
        source_key : str
            The WeeWX key that may be used as a source for derived sensors

        Returns
        -------
        bool
            True if new derived sensors were discovered, False otherwise
        """
        from .utils import get_key_config_dict

        found_derived = False
        logger.debug(f"Checking for derived sensors with source: {source_key}")
        # Find all sensors that have this key as their source
        # Create a snapshot of items to avoid issues if dictionary changes
        for sensor_name, sensor_config in list(get_key_config_dict().items()):
            if sensor_config.get("source") == source_key:
                logger.debug(f"Found sensor {sensor_name} with source {source_key}")
                if sensor_name not in self.seen_measurements:
                    logger.info(
                        f"Discovered derived measurement: {sensor_name} from source {source_key}"
                    )
                    self.seen_measurements[sensor_name] = get_key_config(
                        sensor_name
                    ).copy()
                    # Derived sensors typically don't need unit metadata since they have custom conversion
                    # But if they don't have unit_of_measurement explicitly set, use None
                    if "unit_of_measurement" not in self.seen_measurements[
                        sensor_name
                    ].get("metadata", {}):
                        self.seen_measurements[sensor_name].setdefault("metadata", {})[
                            "unit_of_measurement"
                        ] = None
                    found_derived = True
                else:
                    logger.debug(
                        f"Derived sensor {sensor_name} already in seen_measurements"
                    )

        return found_derived

    def publish_discovery(self) -> None:
        """Publish discovery configurations for Home Assistant.

        This method publishes MQTT discovery configurations for each sensor
        in previously seen measurements to allow Home Assistant to automatically
        discover and configure the sensors.

        Parameters
        ----------
        None

        Returns
        -------
        None
        """
        logger.info(
            f"Publishing {len(self.seen_measurements)} discovery configurations"
        )
        for sensor_name, config in self.seen_measurements.items():
            # Construct discovery topic
            integration = config.get("integration", "sensor")
            discovery_topic = f"{self.discovery_topic_prefix}/{integration}/{self.node_id}/{sensor_name}/config"
            # Construct the configuration payload
            payload: dict[str, Any] = (
                {
                    "availability_topic": self.availability_topic,
                    "state_topic": f"{self.state_topic_prefix}/{sensor_name}",
                    "unique_id": f"{self.node_id}_{sensor_name}",
                }
                | config.get("metadata", {})
                | self.device_description
                | self.origin_description
            )

            # Remove any keys with None values
            payload = {k: v for k, v in payload.items() if v is not None}

            logger.debug(
                f"Publishing discovery configuration: {discovery_topic}: {payload}"
            )
            # Publish the discovery configuration. Retained + QoS 1 so entities
            # survive broker restarts and a lossy/remote MQTT link.
            self.mqtt_client.publish(
                discovery_topic, json.dumps(payload), qos=1, retain=True
            )
