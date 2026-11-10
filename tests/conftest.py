"""pytest plugin configuration.

https://docs.pytest.org/en/latest/writing_plugins.html#conftest-py-plugins
"""

# Third-Party Libraries
import pytest

# Geekpad Libraries
from weewx_ha.locale_loader import set_config_overrides, set_language


@pytest.fixture(autouse=True)
def reset_locale_state():
    """Reset the global locale state around every test.

    The language and the config overrides are module-level globals, and utils
    caches what it builds from them, so a test that sets either one hands the
    next test someone else's catalogue. Seven test modules each carried their
    own copy of this fixture and had to remember to ask for it by name, which
    is a precondition no test can be relied on to declare; autouse here cannot
    be forgotten.
    """
    set_language(None)
    set_config_overrides(None)
    yield
    set_language(None)
    set_config_overrides(None)


def pytest_addoption(parser):
    """Add new commandline options to pytest."""
    parser.addoption(
        "--runslow", action="store_true", default=False, help="run slow tests"
    )


def pytest_configure(config):
    """Register new markers."""
    config.addinivalue_line("markers", "slow: mark test as slow")


def pytest_collection_modifyitems(config, items):
    """Modify collected tests based on custom marks and commandline options."""
    if config.getoption("--runslow"):
        # --runslow given in cli: do not skip slow tests
        return
    skip_slow = pytest.mark.skip(reason="need --runslow option to run")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)
