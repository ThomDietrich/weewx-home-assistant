"""WeeWX extension that publishes station data to Home Assistant over MQTT.

``Controller`` is the service WeeWX loads; the other names are the pieces it
drives, re-exported here for tests and for embedding the publishers directly.

Every module in this package imports from the module that defines a name rather
than from this one. That keeps the package root a facade instead of a hub, so
there is no cycle to work around and no hand-maintained import order: an earlier
revision needed ``isort: off`` here and in ``models`` to keep one alive.
"""

# We disable a Flake8 check for "Module imported but unused (F401)" here because
# although this import is not directly used, it populates the value
# package_name.__version__, which is used to get version information about this
# Python package.
from ._version import __version__  # noqa: F401
from .config_publisher import ConfigPublisher
from .controller import Controller
from .models import ExtensionConfig, MQTTConfig
from .preprocessor import PacketPreprocessor
from .state_publisher import StatePublisher
from .utils import UnitSystem, get_key_config, get_unit_metadata

__all__ = [
    "__version__",
    "ConfigPublisher",
    "Controller",
    "ExtensionConfig",
    "get_key_config",
    "get_unit_metadata",
    "MQTTConfig",
    "PacketPreprocessor",
    "StatePublisher",
    "UnitSystem",
]
