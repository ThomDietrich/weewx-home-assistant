"""Defines the model for validating and managing extension configuration."""

# Standard Python Libraries
from typing import Any, Dict, Optional

# Third-Party Libraries
from pydantic import BaseModel, ConfigDict, Field

from . import MQTTConfig, StationInfo
from .. import UnitSystem


class ExtensionConfig(BaseModel):
    """Configuration model for the extension configuration."""

    model_config = ConfigDict(extra="forbid")

    discovery_topic_prefix: str = Field(
        default="homeassistant", description="Prefix for the MQTT discovery topic"
    )
    enums: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Override enum mappings from enums.yaml",
    )
    lang: Optional[str] = Field(
        default=None,
        pattern=r"^[a-zA-Z]{2,3}(_[a-zA-Z0-9]{2,4})?$",
        description="Language code for localized sensor names (e.g., 'cs', 'en', 'de'). Falls back to English if not set.",
    )
    mqtt: MQTTConfig = Field(..., description="MQTT broker configuration")
    node_id: str = Field(
        ...,
        description="Unique identifier for the Home Assistant node",
        min_length=1,
        pattern=r"^[a-zA-Z0-9_-]+$",
    )
    sensors: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Override sensor configurations from sensors.yaml",
    )
    state_topic_prefix: str = Field(
        default="weather", description="Prefix for the state topic"
    )
    station: StationInfo = Field(..., description="Weather station device information")
    unit_system: UnitSystem = Field(
        default=UnitSystem.METRICWX, description="Unit system for measurements"
    )
    units: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Override unit metadata from units.yaml",
    )

    @classmethod
    def from_config_dict(
        cls, config_dict: Dict[str, Any], key: str
    ) -> "ExtensionConfig":
        """Create an instance from a configuration dictionary."""
        extension_config = config_dict.get(key, {})
        # The ValidationError is deliberately left to propagate untouched: the
        # Controller logs it with the traceback and with what it means for the
        # user ("extension will not be loaded"), and a second report here only
        # buried that message under a duplicate.
        return cls(**extension_config)
