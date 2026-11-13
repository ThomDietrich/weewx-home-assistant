"""Tests for the Controller's runtime paths: LOOP packets, archive records, MQTT.

These exercise the handlers WeeWX and paho actually call. The thread pool is
replaced by an executor that runs the work inline, so the ordering under test
is the ordering the code asks for rather than whatever the scheduler chose.
"""

# Standard Python Libraries
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import MagicMock

# Third-Party Libraries
import pytest
import weewx

# Geekpad Libraries
from weewx_ha import ConfigPublisher, PacketPreprocessor, StatePublisher, UnitSystem
from weewx_ha.controller import Controller
from weewx_ha.models import StationInfo

DISCOVERY_PREFIX = "homeassistant"
STATE_PREFIX = "weather"


class _InlineExecutor:
    """A ThreadPoolExecutor stand-in that runs submitted work immediately."""

    def submit(self, fn, *args):
        future: Future = Future()
        try:
            future.set_result(fn(*args))
        except Exception as error:  # pragma: no cover - surfaced by the future
            future.set_exception(error)
        return future

    def shutdown(self, wait=True):
        """Match the executor interface; there is nothing running to wind down."""


@pytest.fixture
def controller():
    """Build a loaded Controller with no engine, no broker and no threads."""
    controller = Controller.__new__(Controller)
    client = MagicMock()
    client.is_connected.return_value = True
    controller.mqtt_client = client
    controller.executor = _InlineExecutor()
    controller.packet_preprocessor = PacketPreprocessor()
    controller.availability_topic = f"{STATE_PREFIX}/status"
    controller.config = SimpleNamespace(discovery_topic_prefix=DISCOVERY_PREFIX)
    controller.config_publisher = ConfigPublisher(
        client,
        controller.availability_topic,
        DISCOVERY_PREFIX,
        STATE_PREFIX,
        "weewx",
        StationInfo(name="Test Station", model="M", manufacturer="X", time_zone="UTC"),
        UnitSystem.METRICWX,
    )
    controller.state_publisher = StatePublisher(
        client, controller.config_publisher, STATE_PREFIX, UnitSystem.METRICWX
    )
    return controller


def _topics(client):
    """Return the topics published on, in order."""
    return [call.args[0] for call in client.publish.call_args_list if call.args]


def test_the_first_loop_packet_publishes_its_state(controller):
    """A measurement must be published on the packet that discovered it.

    State publishing and discovery registration used to be submitted to the
    pool as two independent tasks. StatePublisher resolves each key against
    ConfigPublisher.seen_measurements and silently drops what it cannot find,
    so on the first packet of a run -- when nothing is registered yet -- every
    value was dropped, and the settle countdown muted the warning that would
    have said so. The same window swallowed the first value of any key
    discovered later.
    """
    controller.on_weewx_loop(
        SimpleNamespace(packet={"usUnits": weewx.US, "outTemp": 68.0})
    )

    topics = _topics(controller.mqtt_client)
    assert f"{STATE_PREFIX}/outTemp" in topics, (
        "the first packet published no state; discovery alone went out: " f"{topics}"
    )


def test_discovery_precedes_the_state_it_describes(controller):
    """Home Assistant cannot accept a state for an entity it has not been told about."""
    controller.on_weewx_loop(
        SimpleNamespace(packet={"usUnits": weewx.US, "outTemp": 68.0})
    )

    topics = _topics(controller.mqtt_client)
    discovery = f"{DISCOVERY_PREFIX}/sensor/weewx/outTemp/config"
    assert discovery in topics, topics
    assert topics.index(discovery) < topics.index(f"{STATE_PREFIX}/outTemp")


def test_a_second_packet_publishes_state_without_re_announcing(controller):
    """Discovery is re-sent only when a packet brings a key that is new."""
    event = SimpleNamespace(packet={"usUnits": weewx.US, "outTemp": 68.0})
    controller.on_weewx_loop(event)
    controller.mqtt_client.publish.reset_mock()

    controller.on_weewx_loop(event)

    topics = _topics(controller.mqtt_client)
    # usUnits carries a sensor config of its own, so it is published as state
    # alongside the measurement; what must not reappear is any discovery topic.
    assert f"{STATE_PREFIX}/outTemp" in topics, topics
    assert not [topic for topic in topics if topic.startswith(DISCOVERY_PREFIX)], topics


def test_an_archive_record_publishes_its_exclusive_measurements(controller):
    """ET is archive-only, so it reaches the broker from the archive handler."""
    controller.engine = SimpleNamespace(
        db_binder=SimpleNamespace(get_manager=MagicMock(side_effect=KeyError("no db")))
    )

    controller.on_weewx_archive(
        SimpleNamespace(
            record={"usUnits": weewx.US, "ET": 0.01, "dateTime": 1782826200}
        )
    )

    assert f"{STATE_PREFIX}/ET" in _topics(controller.mqtt_client)


def test_an_archive_record_with_nothing_exclusive_publishes_nothing(controller):
    """A record carrying only LOOP observations must not overwrite fresher values."""
    controller.engine = SimpleNamespace(
        db_binder=SimpleNamespace(get_manager=MagicMock())
    )

    controller.on_weewx_archive(
        SimpleNamespace(record={"usUnits": weewx.US, "outTemp": 68.0})
    )

    assert _topics(controller.mqtt_client) == []


def test_nothing_is_published_while_the_broker_is_unreachable(controller):
    """With no connection there is nowhere to publish, so no work is submitted."""
    controller.mqtt_client.is_connected.return_value = False

    controller.on_weewx_loop(
        SimpleNamespace(packet={"usUnits": weewx.US, "outTemp": 68.0})
    )

    assert _topics(controller.mqtt_client) == []


def test_the_home_assistant_birth_message_republishes_discovery(controller):
    """Home Assistant restarting must get the entities back without a packet."""
    controller.on_weewx_loop(
        SimpleNamespace(packet={"usUnits": weewx.US, "outTemp": 68.0})
    )
    controller.mqtt_client.publish.reset_mock()

    controller.on_mqtt_message(
        controller.mqtt_client,
        None,
        SimpleNamespace(topic=f"{DISCOVERY_PREFIX}/status", payload=b"online"),
    )

    topics = _topics(controller.mqtt_client)
    assert f"{DISCOVERY_PREFIX}/sensor/weewx/outTemp/config" in topics, topics


def test_an_unrelated_message_republishes_nothing(controller):
    """Only the birth message on the discovery prefix triggers a re-announce."""
    controller.on_weewx_loop(
        SimpleNamespace(packet={"usUnits": weewx.US, "outTemp": 68.0})
    )
    controller.mqtt_client.publish.reset_mock()

    controller.on_mqtt_message(
        controller.mqtt_client,
        None,
        SimpleNamespace(topic=f"{DISCOVERY_PREFIX}/status", payload=b"offline"),
    )

    assert _topics(controller.mqtt_client) == []


def test_no_database_work_while_the_broker_is_unreachable(controller):
    """Seven SQL queries for aggregates nobody can receive are seven too many.

    The archive handler derived its aggregates first and asked about the
    connection afterwards, so every archive interval spent without a broker
    still ran the full set of queries and discarded the answers.
    """
    get_manager = MagicMock()
    controller.engine = SimpleNamespace(
        db_binder=SimpleNamespace(get_manager=get_manager)
    )
    controller.mqtt_client.is_connected.return_value = False

    controller.on_weewx_archive(
        SimpleNamespace(
            record={"usUnits": weewx.US, "ET": 0.01, "dateTime": 1782826200}
        )
    )

    get_manager.assert_not_called()
    assert _topics(controller.mqtt_client) == []
