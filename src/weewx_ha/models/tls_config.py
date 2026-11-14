"""Defines the sub-model for validating and managing the TLS configuration."""

# Standard Python Libraries
import logging
import ssl
from typing import Any

# Third-Party Libraries
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, Secret, model_validator

logger = logging.getLogger(__name__)


class TLSConfig(BaseModel):
    """Pydantic model for secure MQTT TLS configuration defaults."""

    model_config = ConfigDict(extra="forbid")

    cadata: str | None = Field(
        None, description="String containing trusted CA certificates."
    )
    cafile: str | None = Field(
        None, description="Path to a file containing trusted CA certificates."
    )
    capath: str | None = Field(
        None, description="Path to a directory containing trusted CA certificates."
    )
    certfile: str | None = Field(
        None, description="Path to the client certificate file."
    )
    keyfile: str | None = Field(
        None, description="Path to the client private key file."
    )
    password: Secret[str] | None = Field(
        None, description="Password for the client private key file."
    )

    # Define a private attribute for the TLS context
    _context: ssl.SSLContext = PrivateAttr()

    @model_validator(mode="before")
    @classmethod
    def validate_certificate_fields(cls, values: dict[str, Any]) -> dict[str, Any]:
        """Ensure a private key is never configured without its certificate."""
        if values.get("keyfile") and not values.get("certfile"):
            raise ValueError(
                "If 'keyfile' is provided, 'certfile' must also be provided."
            )
        return values

    def __init__(self, **data: Any) -> None:
        """Post-initialization customizations.

        More appropriate than in a model_validator as it is creating complex objects.
        """
        super().__init__(**data)
        # We're going to trust the maintainers of the default context to keep our
        # security settings sane and up-to-date.
        self._context = ssl.create_default_context(
            purpose=ssl.Purpose.SERVER_AUTH,
            cafile=self.cafile,
            capath=self.capath,
            cadata=self.cadata,
        )

        if self.certfile:
            # Keyed on certfile alone, not on both files: ssl takes the private
            # key from the certificate file when keyfile is None, which is how
            # a combined PEM is configured. Requiring both meant a lone
            # certfile was dropped on the floor -- no client certificate was
            # ever presented and nothing said so, so a station configured for
            # mutual TLS silently connected without it.
            logger.debug("Loading the client certificate into the SSL context.")
            try:
                self._context.load_cert_chain(
                    certfile=self.certfile,
                    keyfile=self.keyfile,
                    password=(
                        self.password.get_secret_value() if self.password else None
                    ),
                )
            except (ssl.SSLError, OSError) as error:
                # Names the file that could not be used, instead of letting an
                # SSLError about "PEM lib" reach the log on its own.
                raise ValueError(
                    f"Could not load the client certificate {self.certfile!r}"
                    + (f" with key {self.keyfile!r}" if self.keyfile else "")
                    + f": {error}"
                ) from error

    @property
    def context(self) -> ssl.SSLContext:
        """Provide access to the computed TLS context."""
        return self._context
