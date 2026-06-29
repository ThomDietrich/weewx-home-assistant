# Localization System #

This directory contains YAML configuration files for sensor definitions, unit
metadata, and enum mappings with multi-language support.

## Structure ##

```text
locales/
├── enums.yaml          # Enum mappings (English/fallback)
├── enums_cs.yaml       # Enum mappings (Czech)
├── units.yaml          # Unit metadata (English/fallback)
├── sensors.yaml        # Sensor configurations (English/fallback)
└── README.md           # This file
```

## Language Support ##

The system supports multiple languages through file suffixes:

- **Base files** (no suffix): `enums.yaml`, `sensors.yaml`, `units.yaml` -
  Used as fallback (English)
- **Localized files**: `enums_cs.yaml`, `sensors_cs.yaml`, `units_cs.yaml` -
  Language-specific versions

### Language Selection ###

Language is configured in WeeWX configuration file:

```ini
[HomeAssistant]
    lang = cs  # Use Czech translations
    # lang = en  # Use English (or omit for default)
```

**Loading order:**

1. Load base file (e.g., `sensors.yaml`) as fallback
1. If language is set, load localized file (e.g., `sensors_cs.yaml`)
1. Deep merge: localized values override base values
1. Missing keys in localized file use fallback values

**Partial Localization Support:**
You don't need to translate all sensors! The system supports partial
localization:

- Translate only the sensors you want
- Untranslated sensors automatically use English fallback
- All metadata (icons, device_class, etc.) is preserved from base file

Example `sensors_cs.yaml` (partial):

```yaml
outTemp:
  metadata:
    name: "Venkovní teplota"
    # Other metadata inherited from sensors.yaml

inTemp:
  metadata:
    name: "Vnitřní teplota"

# Other sensors not listed here will use English names from sensors.yaml #
```

Result:

- `outTemp`: "Venkovní teplota" (Czech)
- `inTemp`: "Vnitřní teplota" (Czech)
- `ET`: "Evapotranspiration" (English fallback)
- All sensors have complete metadata (icons, device_class, etc.)

## Files ##

### enums.yaml ###

Defines enum mappings for sensors with `device_class: enum`:

- `cardinal_directions`: Wind direction abbreviations (N, NE, E, etc.)
- `beaufort_scale`: Beaufort wind scale descriptions

### units.yaml ###

Defines unit metadata for different measurement units:

- `unit_of_measurement`: The unit symbol (e.g., "°C", "m/s")
- `suggested_display_precision`: number of decimal places Home Assistant shows
  (e.g., 1). The raw, full-precision value is forwarded; rounding is
  display-only.

### sensors.yaml ###

Defines configuration for each sensor type:

- `metadata`: Home Assistant discovery metadata
  - `name`: Human-readable sensor name
  - `icon`: Material Design Icon (e.g., "mdi:thermometer")
  - `device_class`: Home Assistant device class
  - `state_class`: State class for statistics
  - `unit_of_measurement`: Override unit (usually from units.yaml)
- `convert_lambda`: Reference to conversion function (e.g.,
  "degrees_to_cardinal")
- `source`: Source key for derived sensors (e.g., "windDir" for
  "windDirCardinal")
- `integration`: Override integration type (e.g., "binary_sensor")

## Special Syntax ##

### Enum References ###

Use `@enum_name` to reference enum values:

```yaml
windDirCardinal:
  metadata:
    # Expands to list of cardinal direction values
    options: "@cardinal_directions"
```

### Lambda Functions ###

Reference lambda functions by name in YAML:

```yaml
beaufort:
  # Mapped to Python lambda in _LAMBDA_REGISTRY
  convert_lambda: "beaufort_scale_map"
```

Available lambda functions:

- `beaufort_scale_map`: Maps Beaufort number to scale description
- `degrees_to_cardinal`: Converts degrees to cardinal direction
- `localtime_to_utc_timestamp`: Converts local time to UTC timestamp
- `unit_system_to_string`: Converts WeeWX unit system int to string

## Localization ##

### Full Translation ###

To create a complete localized version:

1. Copy base YAML files with language suffix:

   ```bash
   cp enums.yaml enums_cs.yaml
   cp sensors.yaml sensors_cs.yaml
   cp units.yaml units_cs.yaml
   ```

1. Translate ALL strings in the copied files

1. Configure language in WeeWX config:

   ```ini
   [HomeAssistant]
       lang = cs
   ```

1. Restart WeeWX to load translations

### Partial Translation (Recommended) ###

You can translate only the sensors you need:

1. Create a partial localized file:

   ```bash
   # Create new file (don't copy everything)
   touch enums_cs.yaml
   touch sensors_cs.yaml
   ```

1. Add only the sensors you want to translate:

   ```yaml
   # sensors_cs.yaml
   outTemp:
     metadata:
       name: "Venkovní teplota"

   inTemp:
     metadata:
       name: "Vnitřní teplota"

   # That's it! Other sensors will use English
   ```

1. Configure language (same as full translation)

**Benefits of partial translation:**

- ✓ Less work - translate only what you need
- ✓ Easier maintenance - smaller files to update
- ✓ Mix languages - use English terms for technical sensors
- ✓ Gradual translation - add more over time

### Example: Czech Translation ###

`enums_cs.yaml`:

```yaml
cardinal_directions:
  0: "S"    # Sever
  1: "SSV"
  2: "SV"   # Severovýchod
  # ...

beaufort_scale:
  0: "0 - Bezvětří"
  1: "1 - Téměř bezvětří"
  # ...
```

`sensors_cs.yaml`:

```yaml
outTemp:
  metadata:
    name: "Venkovní teplota"
    # ...other metadata unchanged
```

## Adding New Sensors ##

1. Add sensor definition to `sensors.yaml`:

   ```yaml
   newSensor:
     metadata:
       name: "New Sensor"
       icon: "mdi:icon-name"
       device_class: "temperature"
       state_class: "measurement"
   ```

1. No Python code changes needed - sensor will be automatically loaded!

## Adding New Lambda Functions ##

1. Add function to `_LAMBDA_REGISTRY` in `utils.py`:

   ```python
   _LAMBDA_REGISTRY = {
       "new_converter": lambda x, cp: custom_conversion(x),
   }
   ```

1. Reference in YAML:

   ```yaml
   sensor:
     convert_lambda: "new_converter"
   ```

## Validation ##

Python syntax validation:

```bash
python3 -m py_compile src/weewx_ha/utils.py src/weewx_ha/locale_loader.py
```

YAML syntax validation:

```bash
python3 -c "import yaml; \
  yaml.safe_load(open('src/weewx_ha/locales/sensors.yaml'))"
```

## Benefits ##

- **Easy maintenance**: Edit YAML instead of Python code
- **Localization ready**: Simple to create translations
- **No code changes**: Add/modify sensors without touching Python
- **Version control friendly**: YAML diffs are easy to read
- **Validation**: YAML syntax errors caught at load time
