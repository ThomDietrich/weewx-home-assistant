"""Regression tests for the Controller's behaviour after a rejected config.

``Controller.__init__`` answers an unusable configuration by logging and
returning, which leaves WeeWX holding a service whose attributes were never
assigned. WeeWX still calls ``shutDown`` on it when the engine stops, and that
method publishes an offline message through ``self.mqtt_client`` without
checking whether there is one -- so a configuration mistake turns into an
``AttributeError`` during shutdown, after the real cause has scrolled away.

A disabled service must have one shape, not two: every attribute the lifecycle
methods read exists, and the methods do nothing when there is no client.
"""

# Standard Python Libraries
import logging
from unittest.mock import MagicMock

# Third-Party Libraries
import pytest

# Geekpad Libraries
from weewx_ha.controller import Controller

INVALID_CONFIG: dict = {"HomeAssistant": {"not_a_real_key": 1}}
MISSING_CONFIG: dict = {}

# Attributes the MQTT and WeeWX callbacks read. A disabled Controller must still
# carry every one of them, or the first callback to fire raises AttributeError
# instead of quietly doing nothing.
LIFECYCLE_ATTRIBUTES = (
    "config",
    "availability_topic",
    "mqtt_client",
    "executor",
    "packet_preprocessor",
    "config_publisher",
    "state_publisher",
)


@pytest.fixture
def disabled_controller(request):
    """Build a Controller whose configuration WeeWX would reject."""
    controller = Controller.__new__(Controller)
    engine = MagicMock()
    # Bypass StdService.__init__ so the test does not depend on WeeWX internals,
    # then run the body under test.
    Controller.__init__(controller, engine, request.param)
    return controller


@pytest.mark.parametrize(
    "disabled_controller", [INVALID_CONFIG, MISSING_CONFIG], indirect=True
)
def test_shutdown_after_a_rejected_config_does_not_raise(disabled_controller):
    """Shutting down a service that gave up must not raise."""
    disabled_controller.shutDown()


@pytest.mark.parametrize(
    "disabled_controller", [INVALID_CONFIG, MISSING_CONFIG], indirect=True
)
def test_a_disabled_controller_has_one_attribute_shape(disabled_controller):
    """Every lifecycle attribute exists, so no caller needs a getattr probe."""
    missing = [
        name for name in LIFECYCLE_ATTRIBUTES if not hasattr(disabled_controller, name)
    ]
    assert not missing, f"a disabled Controller is missing {missing}"


@pytest.mark.parametrize(
    "disabled_controller", [INVALID_CONFIG, MISSING_CONFIG], indirect=True
)
def test_a_disabled_controller_publishes_nothing(disabled_controller):
    """Without a client there is nothing to publish to, and nothing is tried."""
    assert disabled_controller.mqtt_client is None
    disabled_controller.shutDown()


def test_the_rejected_config_is_reported_once(caplog):
    """One cause, one error. A second report only buries the first."""
    controller = Controller.__new__(Controller)
    with caplog.at_level(logging.ERROR, logger="weewx_ha"):
        Controller.__init__(controller, MagicMock(), INVALID_CONFIG)
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1, (
        "expected exactly one error for one bad configuration, got: "
        f"{[r.getMessage() for r in errors]}"
    )
    assert errors[0].exc_info, "the surviving report must carry the traceback"
