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
from weeutil.weeutil import startOfDay  # type: ignore
import weewx.units  # type: ignore
from weewx import NEW_ARCHIVE_RECORD, NEW_LOOP_PACKET  # type: ignore
from weewx.engine import StdEngine, StdService  # type: ignore

from . import ConfigPublisher, PacketPreprocessor, StatePublisher
from .locale_loader import set_config_overrides, set_language
from .models import ExtensionConfig, MQTTConfig

logger = logging.getLogger(__name__)

# Register unit groups for the DB-derived rain/ET aggregates published from the
# archive record, so to_std_system converts them (US inch -> METRICWX mm) and
# getStandardUnitType yields the correct unit for discovery.
for _obs in ("hourRain", "rain24", "eventRain", "dayET"):
    weewx.units.obs_group_dict.setdefault(_obs, "group_rain")
for _obs in ("dayMaxOutTemp", "dayMinOutTemp"):
    weewx.units.obs_group_dict.setdefault(_obs, "group_temperature")
weewx.units.obs_group_dict.setdefault("dayMaxWindGust", "group_speed")

# TODO Add command topics to control configuration settings

# Constants
EXTENSION_CONFIG_KEY = "HomeAssistant"
THREAD_POOL_SIZE = 2

# WeeWX archive records are aggregations (averages for intensive types, sums for
# extensive types) of the LOOP packets over the archive interval. Re-publishing
# them wholesale would overwrite the more recent real-time LOOP values in Home
# Assistant. We therefore only publish observations that are exclusive to archive
# records -- i.e. absent or always None in LOOP packets (e.g. ET and windrun, or
# per-interval durations such as sunshineDur/rainDur added by services like
# weewx-sunrainduration). See the WeeWX docs on LOOP vs ARCHIVE. Extend as needed.
ARCHIVE_ONLY_MEASUREMENTS = frozenset({"ET", "windrun", "sunshineDur", "rainDur"})
# Bookkeeping fields kept in the filtered archive record so downstream unit
# conversion (to_std_system) still works. usUnits also has a sensor config, so it
# rides along as a published state value -- harmless, as it is identical to the
# value already published from LOOP packets.
ARCHIVE_PASSTHROUGH_KEYS = frozenset({"usUnits"})

# weewx-sunrainduration emits only the per-interval ``sunshineDur`` (seconds).
# Home Assistant users typically want "sunshine hours today", so we derive a
# cumulative daily total (in hours) from the WeeWX database and publish it as
# ``daySunshineDur``. Reading the day's sum from the DB (rather than keeping an
# in-memory counter) keeps the value correct across WeeWX restarts and resets
# automatically at local midnight. group_deltatime is numerically identical
# across unit systems, so to_std_system leaves the value untouched and the unit
# is overridden to "h" in the sensor metadata (sensors.yaml: daySunshineDur).
DAILY_SUNSHINE_SOURCE = "sunshineDur"
DAILY_SUNSHINE_KEY = "daySunshineDur"
ARCHIVE_DATA_BINDING = "wx_binding"

# Rolling / cumulative aggregates derived from the archive DB (like
# daySunshineDur) so Home Assistant needs no statistics/utility_meter helpers
# that starve when the station reports no change during dry periods.
HOUR_S = 3600
DAY_S = 86400
RAIN_EVENT_GAP_S = 6 * 3600            # >= 6 h dry separates rain events (MIT/IETD)
RAIN_EVENT_LOOKBACK_S = 30 * 86400


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

        self.availability_topic: str = f"{self.config.state_topic_prefix}/status"
        self.mqtt_client: mqtt.Client = self.init_mqtt_client(self.config.mqtt)

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

    def init_mqtt_client(self, mqtt_config: MQTTConfig):
        """Initialize the MQTT client."""
        logger.debug(
            "MQTT configuration: host=%s port=%s tls=%s user=%s",
            mqtt_config.hostname,
            mqtt_config.port,
            mqtt_config.use_tls,
            "<set>" if mqtt_config.username else "<none>",
        )
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, mqtt_config.client_id)
        client.logger = logger
        # Set the callbacks
        client.on_connect = self.on_mqtt_connect
        client.on_message = self.on_mqtt_message
        client.on_subscribe = self.on_mqtt_subscribe
        client.on_unsubscribe = self.on_mqtt_unsubscribe
        client.on_disconnect = self.on_mqtt_disconnect
        if mqtt_config.use_tls:
            client.tls_set_context(mqtt_config.tls.context)
        if mqtt_config.username and mqtt_config.password:
            client.username_pw_set(
                mqtt_config.username, mqtt_config.password.get_secret_value()
            )
        client.loop_start()
        # Set the last will and testament
        client.will_set(self.availability_topic, "offline", qos=1, retain=True)
        client.connect(mqtt_config.hostname, mqtt_config.port, mqtt_config.keep_alive)
        return client

    def on_mqtt_connect(
        self, client: mqtt.Client, userdata, flags, reason_code, properties
    ):
        """Handle callback when the client attempts to connect to the server."""
        if reason_code == 0:
            logger.info("Connected to MQTT broker")
            logger.info("Publishing online availability")
            # Send our birth message
            client.publish(self.availability_topic, "online", qos=1, retain=True)
            # Subscribe to the homeassistant birth message
            client.subscribe(f"{self.config.discovery_topic_prefix}/status", qos=1)
            # Re-publish discovery so entities survive broker/connection drops
            # (important for a remote MQTT link). On the very first connect the
            # publishers may not be constructed yet and no measurements are known,
            # so guard and skip -- discovery then follows from the first packet.
            if getattr(self, "config_publisher", None) is not None:
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
        # Resend config on homeassistant birth
        if (
            msg.topic == f"{self.config.discovery_topic_prefix}/status"
            and msg.payload == b"online"
            and getattr(self, "config_publisher", None) is not None
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
        Only observations exclusive to archive records (ARCHIVE_ONLY_MEASUREMENTS,
        e.g. ET and windrun, which are absent/None in LOOP packets) are published
        from here.
        """
        record_keys = sorted(event.record.keys())
        logger.debug(f"Received WeeWX archive record with keys: {record_keys}")
        filtered = {
            key: value
            for key, value in event.record.items()
            if key in ARCHIVE_ONLY_MEASUREMENTS or key in ARCHIVE_PASSTHROUGH_KEYS
        }
        if (
            not ARCHIVE_ONLY_MEASUREMENTS.intersection(filtered)
            or "usUnits" not in filtered
        ):
            logger.debug(
                "No archive-exclusive measurements (with usUnits) in record; "
                "nothing to publish"
            )
            return
        # Derive and append DB-based aggregates (daily sunshine, rolling rain, ET).
        self._augment_db_derived(event.record, filtered)
        if self.mqtt_client.is_connected():
            preprocessor_future = self.executor.submit(
                self.packet_preprocessor.process_packet, filtered
            )
            preprocessor_future.add_done_callback(self.preprocessor_complete)
        else:
            logger.warning(
                "MQTT client is not connected, skipping archive record processing"
            )

    def _augment_db_derived(self, record: dict, filtered: dict) -> None:
        """Add DB-derived aggregates to ``filtered`` for Home Assistant.

        All values are summed from the WeeWX archive database (authoritative and
        restart-safe), so Home Assistant needs no statistics/utility_meter helpers
        that would starve when the station reports no change:
          - daySunshineDur: cumulative daily sunshine, in hours (from sunshineDur).
          - hourRain / rain24: rolling rainfall over the last 1 h / 24 h.
          - dayET: cumulative evapotranspiration since local midnight.
          - eventRain: rainfall of the most recent event (events separated by a
            >= 6 h dry period, the Minimum Inter-event Time), with eventRainStart,
            eventRainEnd (timestamps) and eventRainDuration (minutes).
          - dayMaxOutTemp / dayMinOutTemp: today's high/low outdoor temperature,
            each with the time it occurred (dayMaxOutTempTime / dayMinOutTempTime).
          - dayMaxWindGust: today's strongest wind gust, with its time
            (dayMaxWindGustTime).
        Rain/ET/temperature/wind values stay in the record's unit system;
        to_std_system converts them downstream. No-op on failure (logged).
        """
        dt = record.get("dateTime")
        if dt is None:
            return
        try:
            manager = self.engine.db_binder.get_manager(ARCHIVE_DATA_BINDING)
            table = manager.table_name
            day_start = startOfDay(dt)

            def _sum(column: str, start: float) -> float:
                row = manager.getSql(
                    f"SELECT SUM({column}) FROM {table} "
                    "WHERE dateTime > ? AND dateTime <= ?",
                    (start, dt),
                )
                return row[0] if row and row[0] is not None else 0.0

            if record.get(DAILY_SUNSHINE_SOURCE) is not None:
                filtered[DAILY_SUNSHINE_KEY] = (
                    _sum(DAILY_SUNSHINE_SOURCE, day_start) / 3600.0
                )
            filtered["hourRain"] = _sum("rain", dt - HOUR_S)
            filtered["rain24"] = _sum("rain", dt - DAY_S)
            filtered["dayET"] = _sum("ET", day_start)
            event_total, event_start, event_end = self._compute_event_rain(
                manager, table, dt
            )
            filtered["eventRain"] = event_total
            if event_start is not None:
                interval_s = (record.get("interval") or 0) * 60
                filtered["eventRainStart"] = event_start
                filtered["eventRainEnd"] = event_end
                filtered["eventRainDuration"] = (
                    event_end - event_start + interval_s
                ) / 60.0
            hi_val, hi_time = self._day_extreme(manager, table, day_start, dt, True)
            if hi_val is not None:
                filtered["dayMaxOutTemp"] = hi_val
                filtered["dayMaxOutTempTime"] = hi_time
            lo_val, lo_time = self._day_extreme(manager, table, day_start, dt, False)
            if lo_val is not None:
                filtered["dayMinOutTemp"] = lo_val
                filtered["dayMinOutTempTime"] = lo_time
            gust_val, gust_time = self._day_extreme(
                manager, table, day_start, dt, True, "windGust"
            )
            if gust_val is not None:
                filtered["dayMaxWindGust"] = gust_val
                filtered["dayMaxWindGustTime"] = gust_time
        except Exception:
            logger.error("Failed to compute DB-derived aggregates", exc_info=True)

    @staticmethod
    def _compute_event_rain(manager, table: str, dt: float):
        """Return ``(total, start, end)`` of the most recent contiguous rain event.

        ``total`` is the summed rainfall; ``start`` and ``end`` are the epoch
        timestamps of the first and last measurable-rain records of that event.
        Rain records separated by >= RAIN_EVENT_GAP_S (6 h) without measurable
        rain belong to different events. Returns ``(0.0, None, None)`` when there
        was no rain within the lookback window.
        """
        recs = [
            (r[0], r[1])
            for r in manager.genSql(
                f"SELECT dateTime, rain FROM {table} "
                "WHERE rain > 0 AND dateTime > ? AND dateTime <= ? "
                "ORDER BY dateTime ASC",
                (dt - RAIN_EVENT_LOOKBACK_S, dt),
            )
            if r[1] is not None
        ]
        if not recs:
            return 0.0, None, None
        start = end = recs[-1][0]
        total = recs[-1][1]
        for i in range(len(recs) - 1, 0, -1):
            if recs[i][0] - recs[i - 1][0] >= RAIN_EVENT_GAP_S:
                break
            total += recs[i - 1][1]
            start = recs[i - 1][0]
        return total, start, end

    @staticmethod
    def _day_extreme(
        manager,
        table: str,
        day_start: float,
        dt: float,
        descending: bool,
        column: str = "outTemp",
    ):
        """Return ``(value, time)`` of today's ``column`` extreme (max if descending).

        ``time`` is the epoch timestamp of the record holding that extreme.
        Returns ``(None, None)`` when the day has no usable ``column`` readings.
        """
        order = "DESC" if descending else "ASC"
        row = manager.getSql(
            f"SELECT {column}, dateTime FROM {table} "
            f"WHERE dateTime > ? AND dateTime <= ? AND {column} IS NOT NULL "
            f"ORDER BY {column} {order}, dateTime ASC LIMIT 1",
            (day_start, dt),
        )
        if row and row[0] is not None:
            return row[0], row[1]
        return None, None

    def shutDown(self):
        """Shutdown the controller.

        This method is overrides the method in StdService class and is called when the extension is unloaded.
        """
        logger.warning("Shutdown requested")

        logger.info("Publishing offline availability")
        message_info = self.mqtt_client.publish(
            self.availability_topic, "offline", qos=1, retain=True
        )
        try:
            message_info.wait_for_publish(timeout=10)
            logger.info("Offline availability publication complete")
        except Exception as e:
            logger.error(f"Error while publishing offline availability: {e}")
        # Shutdown the executor, allowing threads to complete pending work
        self.executor.shutdown(wait=True)
        self.mqtt_client.disconnect()  # Also stops the MQTT client loop
        logger.info("All publisher tasks shut down gracefully.")
