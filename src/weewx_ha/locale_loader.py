"""Loader for localized configuration from YAML files."""

# Standard Python Libraries
from copy import deepcopy
import logging
from pathlib import Path
from typing import Any

# Third-Party Libraries
import yaml  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

# Global language setting (can be set from configuration)
_current_language: str | None = None

# Global config overrides (can be set from configuration)
_config_overrides: dict[str, Any] | None = None


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Deep merge two dictionaries, with overlay taking precedence.

    Parameters
    ----------
    base : dict
        Base dictionary (fallback values)
    overlay : dict
        Overlay dictionary (localized values)

    Returns
    -------
    dict
        Merged dictionary with overlay values taking precedence

    """
    result = deepcopy(base)

    for key, value in overlay.items():
        # Reconcile int vs. str keys: YAML enum maps use integer keys (e.g. `0:`)
        # while config overrides arrive from ConfigObj with string keys (`"0"`).
        # Without this, an override would be appended instead of replacing the
        # base entry, silently no-op'ing enum overrides.
        merge_key = key
        if key not in result:
            if (
                isinstance(key, str)
                and key.lstrip("-").isascii()
                and key.lstrip("-").isdigit()
                and int(key) in result
            ):
                merge_key = int(key)
            elif isinstance(key, int) and str(key) in result:
                merge_key = str(key)

        if (
            merge_key in result
            and isinstance(result[merge_key], dict)
            and isinstance(value, dict)
        ):
            # Recursively merge nested dictionaries
            result[merge_key] = _deep_merge(result[merge_key], value)
        else:
            # Use overlay value
            result[merge_key] = deepcopy(value)

    return result


def set_language(language: str | None) -> None:
    """Set the current language for loading localized YAML files.

    Parameters
    ----------
    language : str | None
        Language code (e.g., 'cs', 'en', 'de') or None for default

    """
    global _current_language
    _current_language = language
    logger.info(f"Language set to: {language or 'default (fallback)'}")


def get_language() -> str | None:
    """Get the current language setting.

    Returns
    -------
    str | None
        Current language code or None

    """
    return _current_language


def set_config_overrides(overrides: dict[str, Any] | None) -> None:
    """Set configuration overrides for locale data.

    Parameters
    ----------
    overrides : dict[str, Any] | None
        Dictionary with keys 'sensors', 'units', 'enums' containing override data

    """
    global _config_overrides
    _config_overrides = overrides
    logger.info(
        f"Config overrides set: {list(overrides.keys()) if overrides else 'None'}"
    )


def get_config_overrides() -> dict[str, Any] | None:
    """Get the current config overrides.

    Returns
    -------
    dict[str, Any] | None
        Current config overrides or None

    """
    return _config_overrides


def load_yaml(base_filename: str, language: str | None = None) -> dict[str, Any]:
    """Load YAML configuration file from locales directory with language fallback.

    Supports partial localization: loads base file first, then merges localized
    file on top, and finally applies config overrides with highest priority.

    Priority order (lowest to highest):
    1. Base YAML file (e.g., sensors.yaml)
    2. Localized YAML file (e.g., sensors_cs.yaml)
    3. Config overrides from weewx configuration

    Parameters
    ----------
    base_filename : str
        Base name of the YAML file (e.g., 'enums.yaml')
    language : str | None
        Language code to try first, or None to use global setting

    Returns
    -------
    dict[str, Any]
        Loaded configuration dictionary (merged if localized file exists)

    """
    locales_dir = Path(__file__).parent / "locales"

    # Use provided language or global setting
    lang = language or _current_language

    # Always load base file as fallback
    base_file_path = locales_dir / base_filename
    base_data: dict[str, Any] = {}

    try:
        with open(base_file_path, encoding="utf-8") as f:
            base_data = yaml.safe_load(f) or {}
            logger.debug(f"Loaded base configuration from {base_file_path}")
    except FileNotFoundError:
        logger.error(f"Base configuration file not found: {base_file_path}")
        return {}
    except yaml.YAMLError as e:
        logger.error(f"Error parsing base YAML file {base_file_path}: {e}")
        return {}

    # Start with base data
    result = base_data

    # If language specified, try to load and merge localized file
    if lang:
        name_parts = base_filename.rsplit(".", maxsplit=1)
        if len(name_parts) == 2:
            name, ext = name_parts
            localized_filename = f"{name}_{lang}.{ext}"
            localized_file_path = locales_dir / localized_filename

            try:
                with open(localized_file_path, encoding="utf-8") as f:
                    localized_data = yaml.safe_load(f) or {}
                    logger.info(
                        f"Loaded localized configuration from {localized_file_path}, "
                        f"merging with base"
                    )
                    # Deep merge: base data with localized overlay
                    result = _deep_merge(result, localized_data)
            except FileNotFoundError:
                logger.debug(
                    f"Localized file not found: {localized_file_path}, using base only"
                )
            except yaml.YAMLError as e:
                logger.error(
                    f"Error parsing localized YAML file {localized_file_path}: {e}, "
                    f"using base only"
                )

    # Apply config overrides if present (highest priority)
    if _config_overrides:
        # Extract override key from filename (e.g., "sensors.yaml" -> "sensors")
        override_key = base_filename.rsplit(".", maxsplit=1)[0]
        if override_key in _config_overrides and _config_overrides[override_key]:
            logger.info(
                f"Applying config overrides for {override_key} "
                f"({len(_config_overrides[override_key])} entries)"
            )
            result = _deep_merge(result, _config_overrides[override_key])

    return result


def load_enums(language: str | None = None) -> dict[str, dict[int, str]]:
    """Load enum mappings from YAML file with language support.

    Parameters
    ----------
    language : str | None
        Language code to load, or None to use global setting

    Returns
    -------
    dict[str, dict[int, str]]
        Dictionary of enum mappings

    """
    return load_yaml("enums.yaml", language)


def load_units(language: str | None = None) -> dict[str, dict[str, Any]]:
    """Load unit metadata from YAML file with language support.

    Parameters
    ----------
    language : str | None
        Language code to load, or None to use global setting

    Returns
    -------
    dict[str, dict[str, Any]]
        Dictionary of unit metadata

    """
    return load_yaml("units.yaml", language)


def load_sensors(language: str | None = None) -> dict[str, Any]:
    """Load sensor configurations from YAML file with language support.

    Parameters
    ----------
    language : str | None
        Language code to load, or None to use global setting

    Returns
    -------
    dict[str, Any]
        Dictionary of sensor configurations

    """
    data = load_yaml("sensors.yaml", language)

    # Process special markers like @cardinal_directions
    enums = load_enums(language)

    def process_value(value: Any) -> Any:
        """Recursively process values to resolve enum references."""
        if isinstance(value, str) and value.startswith("@"):
            # Reference to enum mapping
            enum_name = value[1:]  # Remove @ prefix
            if enum_name in enums:
                return list(enums[enum_name].values())
            logger.warning(f"Unknown enum reference: {value}")
            return value
        elif isinstance(value, dict):
            return {k: process_value(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [process_value(v) for v in value]
        return value

    return {key: process_value(config) for key, config in data.items()}
