"""Regression tests for the data-quality and robustness hardening fixes."""

# Third-Party Libraries
import pytest

# Geekpad Libraries
from weewx_ha.locale_loader import _deep_merge, set_config_overrides, set_language
from weewx_ha.utils import _beaufort_label, get_enum_maps, get_key_config


@pytest.fixture
def reset_locale_state():
    """Reset global locale state before and after each test."""
    set_language(None)
    set_config_overrides(None)
    yield
    set_language(None)
    set_config_overrides(None)


def test_deep_merge_reconciles_str_override_with_int_base():
    """ConfigObj override keys arrive as strings; they must replace int base keys.

    This is the exact path that silently no-op'd enum overrides before the fix.
    """
    base = {0: "N", 1: "NNE"}
    overlay = {"0": "Nord"}
    merged = _deep_merge(base, overlay)
    assert merged[0] == "Nord"
    assert merged[1] == "NNE"
    assert "0" not in merged


def test_deep_merge_reconciles_int_override_with_str_base():
    """The reverse direction (int overlay key, str base key) also reconciles."""
    merged = _deep_merge({"0": "N"}, {0: "Nord"})
    assert merged["0"] == "Nord"
    assert 0 not in merged


def test_deep_merge_preserves_named_string_keys():
    """Non-numeric string keys (sensor/unit names) must be unaffected by coercion."""
    base = {"outTemp": {"metadata": {"name": "Outdoor Temperature"}}}
    overlay = {"outTemp": {"metadata": {"name": "Custom"}}}
    merged = _deep_merge(base, overlay)
    assert merged["outTemp"]["metadata"]["name"] == "Custom"


def test_beaufort_label_clamps_out_of_range(reset_locale_state):
    """Out-of-range forces clamp to a label that is a valid enum option."""
    scale = get_enum_maps()["beaufort_scale"]
    keys = sorted(scale.keys())
    assert _beaufort_label(keys[-1] + 5) == scale[keys[-1]]
    assert _beaufort_label(keys[0] - 5) == scale[keys[0]]


def test_beaufort_label_valid_value(reset_locale_state):
    """In-range forces map to their declared label."""
    scale = get_enum_maps()["beaufort_scale"]
    assert _beaufort_label(0) == scale[0]
    assert _beaufort_label(min(scale.keys()) + 1) == scale[min(scale.keys()) + 1]


def test_get_key_config_returns_isolated_copy(reset_locale_state):
    """Mutating a returned key config must not corrupt the shared cache."""
    first = get_key_config("outTemp")
    first["metadata"]["name"] = "MUTATED"
    second = get_key_config("outTemp")
    assert second["metadata"]["name"] != "MUTATED"
