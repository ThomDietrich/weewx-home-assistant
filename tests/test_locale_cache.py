"""Regression tests for locale changes reaching the utils caches.

``utils`` keeps four module-level lazy caches -- the three YAML documents and
the assembled key config -- and builds each on first use from the
``locale_loader`` globals. ``set_language`` and ``set_config_overrides`` set
those globals, but a cache that is already warm is never rebuilt, so a locale
change made after the first lookup silently has no effect.

Production gets away with it only by call order: the Controller configures the
locale before anything reads a sensor. That is an undocumented requirement on
the caller rather than a property of the module, and it is invisible until
someone reorders two lines. The setters must invalidate what they invalidate.

The ``locale_loader`` loaders are the oracle here, because they read the
globals on every call and have no cache to go stale.
"""

# Third-Party Libraries
import pytest

# Geekpad Libraries
from weewx_ha.locale_loader import load_sensors, set_config_overrides, set_language
from weewx_ha.utils import get_key_config, get_sensors_yaml

LANGUAGE = "de"
KEY = "outTemp"


@pytest.fixture
def warm_caches():
    """Warm the utils caches and hand back the name they were warmed with.

    The base name has to be read here, while no language is set: ``load_sensors``
    treats ``language=None`` as "use the global setting", so there is no way to
    ask it for the base catalogue once a language is in place.
    """
    base_name = load_sensors()[KEY]["metadata"]["name"]
    get_key_config(KEY)
    get_sensors_yaml()
    return base_name


def test_setting_a_language_reaches_a_warm_cache(warm_caches):
    """A language set after the first lookup must still change the names."""
    set_language(LANGUAGE)
    expected = load_sensors(LANGUAGE)[KEY]["metadata"]["name"]
    assert expected != warm_caches, (
        f"the {LANGUAGE} catalogue no longer renames {KEY}, so this test can no "
        "longer tell a stale cache from a fresh one -- pick another key"
    )
    assert get_key_config(KEY)["metadata"]["name"] == expected


def test_setting_overrides_reaches_a_warm_cache(warm_caches):
    """Config overrides set after the first lookup must still apply."""
    name = "Config Override Took Effect"
    set_config_overrides({"sensors": {KEY: {"metadata": {"name": name}}}})
    assert get_key_config(KEY)["metadata"]["name"] == name


def test_clearing_the_locale_restores_the_base_catalogue(warm_caches):
    """Going back to no language and no overrides restores the base names."""
    set_language(LANGUAGE)
    get_key_config(KEY)  # warm the caches again, now holding localized names
    set_language(None)
    assert get_key_config(KEY)["metadata"]["name"] == warm_caches
