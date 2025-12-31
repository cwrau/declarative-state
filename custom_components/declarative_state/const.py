"""Constants and configuration schema for Declarative State."""
from __future__ import annotations

import voluptuous as vol
from homeassistant.const import CONF_CONDITIONS, CONF_NAME, CONF_STATE
from homeassistant.helpers import template as template_helper
import homeassistant.helpers.config_validation as cv

DOMAIN = "declarative_state"


def string_or_template(value):
    """Validate that value is a string (template creation happens later with hass context)."""
    if value is None:
        return None

    # Just validate as string - Template creation happens in sensor.py with hass
    return cv.string(value)

# Configuration keys
CONF_LOOKAHEAD = "lookahead"
CONF_ERROR_HANDLING = "error_handling"
CONF_STATES = "states"
CONF_START = "start"
CONF_END = "end"
CONF_TARGET = "target"
CONF_ACTION = "action"
CONF_DATA = "data"
CONF_EXPECTED_STATE = "expected_state"
CONF_SYNC = "sync"
CONF_SYNC_ATTRIBUTE = "sync_attribute"
CONF_NO_SENSOR = "no_sensor"

# Default values for target
DEFAULT_SYNC = True

# Error handling modes
ERROR_IGNORE = "ignore"
ERROR_UNAVAILABLE = "unavailable"

# Default values
DEFAULT_LOOKAHEAD = 0
DEFAULT_ERROR_HANDLING = ERROR_IGNORE

# Sensor name suffixes
SUFFIX_NEXT = "_next"
SUFFIX_TEMPLATE = "_next_{}"  # e.g., _next_1, _next_2

# Attribute keys
ATTR_START = "start"
ATTR_END = "end"
ATTR_START_TIME = "start_time"
ATTR_END_TIME = "end_time"

# Action configuration schema (for target entity control)
ACTION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ACTION): cv.service,
        vol.Optional(CONF_DATA, default={}): dict,
        vol.Optional(CONF_EXPECTED_STATE): cv.string,
    }
)


def action_config(value):
    """Validate an action config - accepts string or dict."""
    if isinstance(value, str):
        cv.service(value)
        return {CONF_ACTION: value}
    if isinstance(value, dict):
        return ACTION_SCHEMA(value)
    raise vol.Invalid(f"Expected string or dict for action, got {type(value)}")


def _validate_target(value):
    """Validate that at least one of 'action' or 'actions' is present."""
    if CONF_ACTION not in value and "actions" not in value:
        raise vol.Invalid("Target must have at least one of 'action' or 'actions'")
    return value


# Target entity control schema
TARGET_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Required("entity_id"): cv.entity_id,
            vol.Optional(CONF_SYNC, default=DEFAULT_SYNC): cv.boolean,
            vol.Optional(CONF_ACTION): cv.service,
            vol.Optional(CONF_DATA, default={}): dict,
            vol.Optional(CONF_SYNC_ATTRIBUTE): cv.string,
            vol.Optional("actions"): vol.Schema({cv.string: action_config}),
        }
    ),
    _validate_target,
)

# State configuration schema
STATE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_STATE): cv.string,
        vol.Optional(CONF_START): string_or_template,  # Supports templates and plain strings
        vol.Optional(CONF_END): string_or_template,
        vol.Optional(CONF_CONDITIONS): cv.CONDITIONS_SCHEMA,
    }
)

# Platform schema for YAML configuration
PLATFORM_SCHEMA = cv.PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_NAME): cv.string,
        vol.Optional(CONF_LOOKAHEAD, default=DEFAULT_LOOKAHEAD): vol.All(
            cv.positive_int, vol.Range(min=0)
        ),
        vol.Optional(
            CONF_ERROR_HANDLING, default=DEFAULT_ERROR_HANDLING
        ): vol.In([ERROR_IGNORE, ERROR_UNAVAILABLE]),
        vol.Optional(CONF_TARGET): TARGET_SCHEMA,
        vol.Required(CONF_STATES): vol.All(cv.ensure_list, [STATE_SCHEMA]),
    }
)
