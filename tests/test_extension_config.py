"""Tests for ``ExtensionConfig.from_config_dict``, the entry point WeeWX uses.

The Controller hands this method the whole weewx.conf dictionary and takes the
result as the extension's configuration, so this is where a user's typo turns
into either a clear refusal or a service that loads with silent defaults. Both
outcomes are asserted here, including the empty-stanza case: a weewx.conf with
no ``[HomeAssistant]`` section must be refused, not papered over with defaults.

The method must also stay quiet. The Controller logs the rejection with the
traceback and with what it means for the user ("extension will not be loaded");
a second report from the model buried that message under a duplicate, so this
pins the absence of one.
"""

# Standard Python Libraries
import logging

# Third-Party Libraries
from pydantic import ValidationError
import pytest

# Geekpad Libraries
from weewx_ha import UnitSystem
from weewx_ha.models import ExtensionConfig

KEY = "HomeAssistant"
NODE_ID = "test_node"

VALID_STANZA = {
    "node_id": NODE_ID,
    "mqtt": {
        "hostname": "broker.example.test",
        "username": "weewx",
        "password": "secret",
    },
    "station": {
        "name": "Test Station",
        "model": "Vantage Pro2",
        "manufacturer": "Davis",
    },
}


def _config_dict(**stanza_overrides):
    stanza = dict(VALID_STANZA)
    stanza.update(stanza_overrides)
    return {KEY: stanza, "SomeOtherService": {"enable": "true"}}


def test_a_valid_stanza_is_accepted_with_its_defaults():
    """A good stanza yields the configuration, including the unset defaults."""
    config = ExtensionConfig.from_config_dict(_config_dict(), KEY)
    assert config.node_id == NODE_ID
    assert config.unit_system is UnitSystem.METRICWX
    assert config.discovery_topic_prefix == "homeassistant"
    assert config.state_topic_prefix == "weather"


def test_an_unknown_key_is_refused():
    """extra='forbid' is the point: a typo must not be ignored."""
    with pytest.raises(ValidationError):
        ExtensionConfig.from_config_dict(_config_dict(not_a_real_key=1), KEY)


def test_a_missing_stanza_is_refused_rather_than_defaulted():
    """No [HomeAssistant] section means no configuration, not an empty one.

    ``config_dict.get(key, {})`` would hand an empty stanza to the model, and
    the model has to be the one that refuses it -- node_id, mqtt and station
    are all required, so it does.
    """
    with pytest.raises(ValidationError):
        ExtensionConfig.from_config_dict({"SomeOtherService": {}}, KEY)


@pytest.mark.parametrize(
    "config_dict",
    [_config_dict(not_a_real_key=1), {}],
    ids=["unknown-key", "missing-stanza"],
)
def test_the_model_reports_nothing_itself(config_dict, caplog):
    """The refusal is the Controller's to report; a second copy only buries it."""
    with caplog.at_level(logging.DEBUG, logger="weewx_ha.models.extension_config"):
        with pytest.raises(ValidationError):
            ExtensionConfig.from_config_dict(config_dict, KEY)
    records = [
        r
        for r in caplog.records
        if r.name == "weewx_ha.models.extension_config" and r.levelno >= logging.WARNING
    ]
    assert not records, f"the model reported the rejection itself: {records}"
