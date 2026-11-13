"""Tests for the German (de) locale: completeness and validity."""

# Standard Python Libraries
from collections import Counter
from pathlib import Path

# Third-Party Libraries
import yaml

# Geekpad Libraries
import weewx_ha
from weewx_ha.locale_loader import load_enums, load_sensors, set_language

_LOCALES_DIR = Path(weewx_ha.__file__).parent / "locales"


def test_de_translates_every_sensor():
    """Every base sensor must have an explicit German name entry.

    Checks the de YAML file directly (not the merged result) so that loanwords
    that are legitimately identical to English (e.g. "Humidex") still count as
    translated, while a forgotten key is caught.
    """
    set_language(None)
    base = load_sensors()
    de_file = yaml.safe_load(
        (_LOCALES_DIR / "sensors_de.yaml").read_text(encoding="utf-8")
    )
    missing = [
        key
        for key in base
        if key not in de_file
        or not (de_file[key] or {}).get("metadata", {}).get("name")
    ]
    assert not missing, f"Sensors missing from the German locale: {missing}"


def test_de_has_no_duplicate_enabled_names():
    """German display names must stay unique among enabled sensors."""
    set_language("de")
    de = load_sensors()
    names: Counter = Counter()
    for cfg in de.values():
        md = (cfg or {}).get("metadata", {})
        if md.get("enabled_by_default", True):
            names[md.get("name")] += 1
    dups = {n: c for n, c in names.items() if c > 1}
    assert not dups, f"Duplicate enabled German names: {dups}"


def test_de_enums_localized():
    """Cardinal directions and Beaufort scale are localized to German."""
    set_language("de")
    enums = load_enums()
    assert enums["cardinal_directions"][2] == "NO"
    assert enums["cardinal_directions"][6] == "SO"
    assert "Orkan" in enums["beaufort_scale"][12]
    assert "Windstille" in enums["beaufort_scale"][0]
