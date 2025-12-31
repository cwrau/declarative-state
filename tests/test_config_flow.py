"""Tests for the config flow."""
import pytest
from unittest.mock import patch
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.declarative_state.const import (
    DOMAIN,
    CONF_NAME,
    CONF_LOOKAHEAD,
    CONF_ERROR_HANDLING,
    CONF_STATES,
    CONF_STATE,
    CONF_START,
    CONF_END,
    ERROR_IGNORE,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations."""
    yield


async def test_user_flow_success(hass: HomeAssistant):
    """Test successful user flow with single state."""
    # Step 1: Initialize flow
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    # Step 2: Submit user data
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "test_sensor",
            CONF_LOOKAHEAD: 2,
            CONF_ERROR_HANDLING: ERROR_IGNORE,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "states"

    # Step 3: States step - add one state without "add_another"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_STATE: "on",
            CONF_START: "08:00",
            CONF_END: "17:00",
            "add_another": False,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "test_sensor"
    assert result["data"][CONF_NAME] == "test_sensor"
    assert result["options"][CONF_LOOKAHEAD] == 2
    assert result["options"][CONF_ERROR_HANDLING] == ERROR_IGNORE
    assert len(result["options"][CONF_STATES]) == 1
    assert result["options"][CONF_STATES][0][CONF_STATE] == "on"
    assert result["options"][CONF_STATES][0][CONF_START] == "08:00"
    assert result["options"][CONF_STATES][0][CONF_END] == "17:00"
    assert "id" in result["options"][CONF_STATES][0]  # UUID generated


async def test_user_flow_duplicate_name(hass: HomeAssistant):
    """Test that duplicate sensor names are prevented."""
    # Create first entry
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "test_sensor", CONF_LOOKAHEAD: 2, CONF_ERROR_HANDLING: ERROR_IGNORE},
    )
    await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_STATE: "on", CONF_START: "08:00", CONF_END: "17:00", "add_another": False},
    )

    # Try to create second entry with same name
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "test_sensor", CONF_LOOKAHEAD: 1, CONF_ERROR_HANDLING: ERROR_IGNORE},
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_states_flow_multiple_states(hass: HomeAssistant):
    """Test adding multiple states with add_another checkbox."""
    # Step 1: Initialize and configure basic settings
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "test_sensor", CONF_LOOKAHEAD: 1, CONF_ERROR_HANDLING: ERROR_IGNORE},
    )

    # Step 2a: Add first state with add_another=True
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_STATE: "on", CONF_START: "08:00", CONF_END: "17:00", "add_another": True},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "states"

    # Step 2b: Add second state with add_another=True
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_STATE: "off", CONF_START: "17:00", CONF_END: "08:00", "add_another": True},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "states"

    # Step 2c: Add third state with add_another=False (finish)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_STATE: "standby", CONF_START: "", CONF_END: "", "add_another": False},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert len(result["options"][CONF_STATES]) == 3
    assert result["options"][CONF_STATES][0][CONF_STATE] == "on"
    assert result["options"][CONF_STATES][1][CONF_STATE] == "off"
    assert result["options"][CONF_STATES][2][CONF_STATE] == "standby"


async def test_states_flow_invalid_time(hass: HomeAssistant):
    """Test validation of invalid time formats."""
    # Step 1: Initialize and configure basic settings
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "test_sensor", CONF_LOOKAHEAD: 1, CONF_ERROR_HANDLING: ERROR_IGNORE},
    )

    # Step 2: Submit invalid time format
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_STATE: "on",
            CONF_START: "invalid-time",
            CONF_END: "17:00",
            "add_another": False,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "states"
    assert CONF_START in result["errors"]
    assert result["errors"][CONF_START] == "invalid_time_format"


async def test_import_flow_yaml(hass: HomeAssistant):
    """Test YAML import flow adds UUIDs to states."""
    # YAML config
    yaml_config = {
        CONF_NAME: "yaml_sensor",
        CONF_LOOKAHEAD: 3,
        CONF_ERROR_HANDLING: ERROR_IGNORE,
        CONF_STATES: [
            {CONF_STATE: "on", CONF_START: "08:00", CONF_END: "17:00"},
            {CONF_STATE: "off", CONF_START: "17:00", CONF_END: "08:00"},
        ],
    }

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_IMPORT}, data=yaml_config
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "yaml_sensor"
    assert result["data"][CONF_NAME] == "yaml_sensor"
    assert result["options"][CONF_LOOKAHEAD] == 3
    assert len(result["options"][CONF_STATES]) == 2
    # Verify UUIDs were added
    assert "id" in result["options"][CONF_STATES][0]
    assert "id" in result["options"][CONF_STATES][1]
    # Verify states preserved
    assert result["options"][CONF_STATES][0][CONF_STATE] == "on"
    assert result["options"][CONF_STATES][1][CONF_STATE] == "off"


async def test_states_flow_empty_times(hass: HomeAssistant):
    """Test that empty start/end times are handled (always active)."""
    # Step 1: Initialize and configure basic settings
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "test_sensor", CONF_LOOKAHEAD: 0, CONF_ERROR_HANDLING: ERROR_IGNORE},
    )

    # Step 2: Submit state with empty start/end
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_STATE: "always_on", CONF_START: "", CONF_END: "", "add_another": False},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert len(result["options"][CONF_STATES]) == 1
    # Empty strings should be stored as empty strings
    assert result["options"][CONF_STATES][0][CONF_START] == ""
    assert result["options"][CONF_STATES][0][CONF_END] == ""


async def test_options_flow_init_menu(hass: HomeAssistant):
    """Test options flow shows initial menu."""
    # Create config entry
    entry = hass.config_entries.async_entries(DOMAIN)[0] if hass.config_entries.async_entries(DOMAIN) else None
    if not entry:
        # Create entry first
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_NAME: "test_sensor", CONF_LOOKAHEAD: 2, CONF_ERROR_HANDLING: ERROR_IGNORE},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_STATE: "on", CONF_START: "08:00", CONF_END: "17:00", "add_another": False},
        )
        # Get the created entry
        await hass.async_block_till_done()
        entry = hass.config_entries.async_entries(DOMAIN)[0]

    # Start options flow
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "init"
    assert "settings" in result["menu_options"]
    assert "manage_states" in result["menu_options"]
    assert "done" in result["menu_options"]


async def test_options_flow_settings(hass: HomeAssistant):
    """Test editing general settings."""
    # Create entry
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "test_sensor", CONF_LOOKAHEAD: 2, CONF_ERROR_HANDLING: ERROR_IGNORE},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_STATE: "on", CONF_START: "08:00", CONF_END: "17:00", "add_another": False},
    )
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    # Start options flow and navigate to settings
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "settings"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "settings"

    # Update settings
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_LOOKAHEAD: 5, CONF_ERROR_HANDLING: "unavailable"},
    )
    # After updating settings, it returns to main menu
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "init"

    # Verify settings were updated
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.options[CONF_LOOKAHEAD] == 5
    assert entry.options[CONF_ERROR_HANDLING] == "unavailable"


async def test_options_flow_add_state(hass: HomeAssistant):
    """Test adding a state via options flow."""
    # Create entry with one state
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "test_sensor", CONF_LOOKAHEAD: 2, CONF_ERROR_HANDLING: ERROR_IGNORE},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_STATE: "on", CONF_START: "08:00", CONF_END: "17:00", "add_another": False},
    )
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    # Navigate to manage_states -> add_state
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "manage_states"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_state"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "add_state"

    # Submit new state
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_STATE: "off", CONF_START: "17:00", CONF_END: "08:00"},
    )
    # After adding state, it returns to manage states menu
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "manage_states"

    # Verify state was added
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert len(entry.options[CONF_STATES]) == 2
    assert entry.options[CONF_STATES][1][CONF_STATE] == "off"
    assert "id" in entry.options[CONF_STATES][1]  # UUID added


async def test_options_flow_done(hass: HomeAssistant):
    """Test selecting Done returns correct result."""
    # Create entry
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "test_sensor", CONF_LOOKAHEAD: 2, CONF_ERROR_HANDLING: ERROR_IGNORE},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_STATE: "on", CONF_START: "08:00", CONF_END: "17:00", "add_another": False},
    )
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    # Select "done" from main menu
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "done"}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
