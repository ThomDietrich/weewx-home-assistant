"""
Integrates WeeWX with Home Assistant via MQTT.

This module defines the Controller class. The Controller class extends the
StdService class from WeeWX and manages the MQTT client, publishes state and
configuration data, and handles WeeWX loop packets.
"""

# Standard Python Libraries
from concurrent.futures import ThreadPoolExecutor
import logging
from typing import Any

# Third-Party Libraries
import paho.mqtt.client as mqtt
from weewx import NEW_ARCHIVE_RECORD, NEW_LOOP_PACKET  # type: ignore
from weewx.engine import StdEngine, StdService  # type: ignore

from . import archive
from .config_publisher import ConfigPublisher
from .locale_loader import set_config_overrides, set_language
from .models import ExtensionConfig, MQTTConfig
from .mqtt_session import build_client
from .preprocessor import PacketPreprocessor
from .state_publisher import StatePublisher

logger = logging.getLogger(__name__)

# TODO Add command topics to control configuration settings

# Constants
EXTENSION_CONFIG_KEY = "HomeAssistant"
THREAD_POOL_SIZE = 2


class Controller(StdService):
    """Controller class for the Home Assistant MQTT extension."""

    def __init__(self, engine: StdEngine, config_dict: dict[Any, Any]):
        """Initialize the controller.

        Args:
            engine: The WeeWX engine
            config_dict: The configuration dictionary
        """
        super().__init__(engine, config_dict)
        logger.debug(
            f"Initializing extension with configuration key {EXTENSION_CONFIG_KEY}"
        )
        # Declare the disabled shape up front. WeeWX keeps a service whose
        # constructor returned early and still calls shutDown on it when the
        # engine stops, so an unusable configuration must leave the instance
        # with every attribute the lifecycle methods read -- otherwise a config
        # mistake resurfaces as an AttributeError long after its own report has
        # scrolled past. Assigning here rather than in the except branch also
        # keeps one declared type per attribute.
        self.config: ExtensionConfig | None = None
        self.availability_topic: str | None = None
        self.mqtt_client: mqtt.Client | None = None
        self.executor: ThreadPoolExecutor | None = None
        self.packet_preprocessor: PacketPreprocessor | None = None
        self.config_publisher: ConfigPublisher | None = None
        self.state_publisher: StatePublisher | None = None
        try:
            self.config = ExtensionConfig.from_config_dict(
                config_dict, EXTENSION_CONFIG_KEY
            )
        except Exception:
            logger.error(
                "Invalid or missing extension configuration. Extension will not be loaded.",
                exc_info=True,
            )
            return
        logger.debug(
            f"Loaded extension configuration:\n{self.config.model_dump_json(indent=4)}"
        )

        # Set language for localized YAML loading
        set_language(self.config.lang)

        # Set config overrides if provided
        overrides = {}
        if self.config.sensors:
            overrides["sensors"] = self.config.sensors
        if self.config.units:
            overrides["units"] = self.config.units
        if self.config.enums:
            overrides["enums"] = self.config.enums
        set_config_overrides(overrides if overrides else None)

        self.availability_topic = f"{self.config.state_topic_prefix}/status"
        self.mqtt_client = self.init_mqtt_client(
            self.config.mqtt, self.availability_topic
        )

        # Thread pool for managing tasks
        self.executor = ThreadPoolExecutor(max_workers=THREAD_POOL_SIZE)

        # Create packet preprocessor
        self.packet_preprocessor = PacketPreprocessor()

        # Create a publishers
        self.config_publisher = ConfigPublisher(
            self.mqtt_client,
            self.availability_topic,
            self.config.discovery_topic_prefix,
            self.config.state_topic_prefix,
            self.config.node_id,
            self.config.station,
            self.config.unit_system,
        )

        self.state_publisher = StatePublisher(
            self.mqtt_client,
            self.config_publisher,
            self.config.state_topic_prefix,
            self.config.unit_system,
        )

        # Register the callbacks for loop packets and archive records
        self.bind(NEW_LOOP_PACKET, self.on_weewx_loop)
        self.bind(NEW_ARCHIVE_RECORD, self.on_weewx_archive)

    def init_mqtt_client(self, mqtt_config: MQTTConfig, availability_topic: str):
        """Initialize the MQTT client."""
        return build_client(mqtt_config, availability_topic, self)

    def on_mqtt_connect(
        self, client: mqtt.Client, userdata, flags, reason_code, properties
    ):
        """Handle callback when the client attempts to connect to the server."""
        if self.config is None or self.availability_topic is None:
            return  # configuration was rejected; the extension is not loaded
        if reason_code == 0:
            logger.info("Connected to MQTT broker")
            logger.info("Publishing online availability")
            # Send our birth message
            client.publish(self.availability_topic, "online", qos=1, retain=True)
            # Subscribe to the homeassistant birth message
            client.subscribe(f"{self.config.discovery_topic_prefix}/status", qos=1)
            # Re-publish discovery so entities survive broker/connection drops
            # (important for a remote MQTT link). On the very first connect the
            # publishers are not constructed yet and no measurements are known,
            # so skip -- discovery then follows from the first packet.
            if self.config_publisher is not None and self.executor is not None:
                future = self.executor.submit(self.config_publisher.publish_discovery)
                future.add_done_callback(self.check_future_errors)
        else:
            logger.error(f"Failed to connect to MQTT broker, return code {reason_code}")

    def on_mqtt_connect_fail(self, client: mqtt.Client, userdata):
        """Handle callback when the client fails to connect to the server."""
        logger.error("Failed to connect to MQTT broker")

    def on_mqtt_disconnect(
        self, client: mqtt.Client, userdata, disconnect_flags, reason_code, properties
    ):
        """Handle callback for when the client disconnects from the server."""
        if reason_code != 0:
            logger.warning(
                f"Unexpected disconnection from MQTT broker, return code {reason_code}, disconnect flags {disconnect_flags}"
            )
        else:
            logger.info("Disconnected from MQTT broker")

    def on_mqtt_message(self, client: mqtt.Client, userdata, msg):
        """Handle callback for when a PUBLISH message is received from the server."""
        logger.info(f"Received message on topic {msg.topic}: {msg.payload}")
        if self.config is None or self.config_publisher is None:
            return  # configuration was rejected; the extension is not loaded
        # Resend config on homeassistant birth
        if (
            msg.topic == f"{self.config.discovery_topic_prefix}/status"
            and msg.payload == b"online"
            and self.executor is not None
        ):
            future = self.executor.submit(self.config_publisher.publish_discovery)
            future.add_done_callback(self.check_future_errors)

    def on_mqtt_subscribe(
        self, client: mqtt.Client, userdata, mid, reason_code_list, properties
    ):
        """Handle callback for when the broker responds to a subscribe request."""
        logger.info(f"Subscribed to topic, message ID: {mid}")

    def on_mqtt_unsubscribe(
        self, client: mqtt.Client, userdata, mid, reason_code_list, properties
    ):
        """Handle callback for when the broker responds to an unsubscribe request."""
        logger.info(f"Unsubscribed from topic, message ID: {mid}")

    def check_future_errors(self, future):
        """Handle callback and check for exceptions in a Future."""
        try:
            future.result()
        except Exception as e:
            logger.error(f"Error in future: {e}", exc_info=True)

    def check_config_update(self, future):
        """Check if a config update is needed after processing a loop packet."""
        try:
            # Get the result of the Future, which will be True or False
            needs_publish: bool = future.result()
        except Exception as e:
            logger.error(f"Error while checking config update: {e}", exc_info=True)
            return
        if needs_publish:
            logger.debug("New measurements found, submitting config update task")
            future2 = self.executor.submit(self.config_publisher.publish_discovery)
            future2.add_done_callback(self.check_future_errors)

    def preprocessor_complete(self, future):
        """Handle callback for when the preprocessor task is complete."""
        try:
            packet: dict = future.result()
        except Exception as e:
            logger.error(f"Error while pre-processing packet: {e}", exc_info=True)
            return
        state_future = self.executor.submit(self.state_publisher.process_packet, packet)
        # Add callback to state publishing task
        state_future.add_done_callback(self.check_future_errors)
        config_future = self.executor.submit(
            self.config_publisher.process_packet, packet
        )
        # Add callbacks to config processing task
        config_future.add_done_callback(self.check_config_update)

    def on_weewx_loop(self, event):
        """Handle callback for WeeWX loop packets."""
        packet_keys = sorted(event.packet.keys())
        logger.debug(f"Received WeeWX loop packet with keys: {packet_keys}")
        if self.mqtt_client.is_connected():
            preprocessor_future = self.executor.submit(
                self.packet_preprocessor.process_packet, event.packet.copy()
            )
            preprocessor_future.add_done_callback(self.preprocessor_complete)
        else:
            logger.warning("MQTT client is not connected, skipping packet processing")

    def on_weewx_archive(self, event):
        """Handle callback for WeeWX archive records.

        Archive records aggregate LOOP packets, so re-publishing them wholesale
        would overwrite the more recent real-time LOOP values in Home Assistant.
        Only observations exclusive to archive records, plus the aggregates
        ``archive`` derives from the database, are published from here.
        """
        record_keys = sorted(event.record.keys())
        logger.debug(f"Received WeeWX archive record with keys: {record_keys}")
        publishable = archive.select_publishable(event.record)
        if publishable is None:
            logger.debug(
                "No archive-exclusive measurements (with usUnits) in record; "
                "nothing to publish"
            )
            return
        # Derive and append DB-based aggregates (daily sunshine, rolling rain, ET).
        publishable.update(archive.derive_from_db(self.engine.db_binder, event.record))
        if self.mqtt_client.is_connected():
            preprocessor_future = self.executor.submit(
                self.packet_preprocessor.process_packet, publishable
            )
            preprocessor_future.add_done_callback(self.preprocessor_complete)
        else:
            logger.warning(
                "MQTT client is not connected, skipping archive record processing"
            )

    def shutDown(self):
        """Shutdown the controller.

        This method is overrides the method in StdService class and is called when the extension is unloaded.
        """
        logger.warning("Shutdown requested")

        if self.mqtt_client is None or self.executor is None:
            # The configuration was rejected, so there is no broker
            # connection and no worker pool to wind down.
            logger.info("Extension was never loaded; nothing to shut down")
            return

        logger.info("Publishing offline availability")
        message_info = self.mqtt_client.publish(
            self.availability_topic, "offline", qos=1, retain=True
        )
        try:
            message_info.wait_for_publish(timeout=10)
            logger.info("Offline availability publication complete")
        except Exception as e:
            logger.error(
                f"Error while publishing offline availability: {e}", exc_info=True
            )
        # Shutdown the executor, allowing threads to complete pending work
        self.executor.shutdown(wait=True)
        self.mqtt_client.disconnect()  # Also stops the MQTT client loop
        logger.info("All publisher tasks shut down gracefully.")
