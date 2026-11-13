"""Defines the model for describing a weather station device."""

# Standard Python Libraries
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Third-Party Libraries
from pydantic import BaseModel, ConfigDict, Field, field_validator


class StationInfo(BaseModel):
    """Model for describing a weather station device."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(..., description="Name of the weather station.", min_length=1)
    model: str = Field(..., description="Model of the weather station.", min_length=1)
    manufacturer: str = Field(
        ..., description="Manufacturer of the weather station.", min_length=1
    )
    time_zone: ZoneInfo = Field(
        default_factory=lambda: ZoneInfo("UTC"),
        description="Unused. WeeWX timestamps are Unix epochs and are "
        "converted straight to UTC, so no station time zone is needed. Still "
        "accepted so that existing configurations keep validating.",
        alias="timezone",
    )

    @field_validator("time_zone", mode="before")
    @classmethod
    def validate_time_zone(cls, value: Any) -> ZoneInfo:
        """Coerce the configured time zone into a ZoneInfo, or reject it.

        ``value`` is whatever the configuration carried, so a name that is not
        a zone and a value that is not a name both have to come back as a
        validation error naming the offending input -- with the original
        exception chained, so the traceback still says what went wrong.
        """
        if isinstance(value, ZoneInfo):
            return value
        try:
            return ZoneInfo(value)
        except (ZoneInfoNotFoundError, TypeError, ValueError) as error:
            raise ValueError(f"Invalid time zone: {value!r}") from error
