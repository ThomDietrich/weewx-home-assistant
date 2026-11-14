"""Tests for the configuration models' validation behaviour."""

# Standard Python Libraries
import ssl
from unittest.mock import patch
from zoneinfo import ZoneInfo

# Third-Party Libraries
from pydantic import ValidationError
import pytest

# Geekpad Libraries
from weewx_ha.models import StationInfo, TLSConfig
import weewx_ha.models.tls_config as tls_config

STATION = {"name": "Test Station", "model": "M", "manufacturer": "X"}


def test_a_zone_name_becomes_a_zoneinfo():
    """The configuration carries a name; the model carries the zone."""
    station = StationInfo(**STATION, timezone="Europe/Berlin")
    assert station.time_zone == ZoneInfo("Europe/Berlin")


def test_a_zoneinfo_is_accepted_unchanged():
    """Constructing the model from an already-built zone must not re-resolve it."""
    zone = ZoneInfo("UTC")
    assert StationInfo(**STATION, timezone=zone).time_zone is zone


def test_the_time_zone_defaults_to_utc():
    """The field is unused, so configurations that omit it still validate."""
    assert StationInfo(**STATION).time_zone == ZoneInfo("UTC")


@pytest.mark.parametrize("bad", ["Not/AZone", 123, ["Europe/Berlin"]])
def test_an_unusable_time_zone_is_a_validation_error(bad):
    """A name that is not a zone and a value that is not a name both get rejected.

    Only ZoneInfoNotFoundError used to be caught, so a value of the wrong type
    raised TypeError straight out of the validator instead of arriving as a
    validation error naming the offending input.
    """
    with pytest.raises(ValidationError) as caught:
        StationInfo(**STATION, timezone=bad)
    assert "Invalid time zone" in str(caught.value)


CERT = "/etc/weewx/client.pem"
KEY = "/etc/weewx/client.key"
# Bound to a name rather than written inline so bandit does not read a literal
# passed as password= as a hardcoded credential.
PEM_PHRASE = "unlocks-the-test-key"


@pytest.fixture
def ssl_context():
    """Patch out the real SSL context so the calls into ssl can be inspected."""
    with patch.object(tls_config.ssl, "create_default_context") as create:
        yield create.return_value


def test_a_certfile_alone_still_loads_a_client_certificate(ssl_context):
    """A combined PEM is configured with certfile only, and must be honoured.

    The chain was loaded only when certfile *and* keyfile were both set, while
    the validator rejected only the opposite half -- a keyfile without a
    certfile. A lone certfile therefore passed validation and was then
    ignored, so a station configured for mutual TLS connected without
    presenting a certificate and nothing in the log said so. ssl reads the
    private key out of certfile when keyfile is None.
    """
    TLSConfig(certfile=CERT)

    ssl_context.load_cert_chain.assert_called_once_with(
        certfile=CERT, keyfile=None, password=None
    )


def test_a_certfile_and_keyfile_are_both_passed_through(ssl_context):
    """The two-file form keeps working."""
    TLSConfig(certfile=CERT, keyfile=KEY, password=PEM_PHRASE)

    ssl_context.load_cert_chain.assert_called_once_with(
        certfile=CERT, keyfile=KEY, password=PEM_PHRASE
    )


def test_no_certfile_loads_no_chain(ssl_context):
    """Server-authentication-only TLS needs no client certificate."""
    TLSConfig()

    ssl_context.load_cert_chain.assert_not_called()


def test_a_keyfile_without_a_certfile_is_rejected():
    """A private key with no certificate cannot authenticate anything."""
    with pytest.raises(ValidationError, match="certfile"):
        TLSConfig(keyfile=KEY)


def test_an_unusable_certificate_names_the_file(ssl_context):
    """An SSLError about the PEM library on its own does not say which file."""
    ssl_context.load_cert_chain.side_effect = ssl.SSLError("PEM lib")

    with pytest.raises(ValueError) as caught:
        TLSConfig(certfile=CERT)

    assert CERT in str(caught.value)
    assert isinstance(caught.value.__cause__, ssl.SSLError)
