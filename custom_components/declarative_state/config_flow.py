"""Config flow for Declarative State integration."""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult, section
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.selector import ActionSelector, ConditionSelector, NumberSelector, NumberSelectorConfig, NumberSelectorMode, TargetSelector

from .const import (
    CONF_ACTION,
    CONF_CONDITIONS,
    CONF_DATA,
    CONF_END,
    CONF_ERROR_HANDLING,
    CONF_EXPECTED_STATE,
    CONF_LOOKAHEAD,
    CONF_START,
    CONF_STATE,
    CONF_STATES,
    CONF_NO_SENSOR,
    CONF_SYNC,
    CONF_SYNC_ATTRIBUTE,
    CONF_TARGET,
    DEFAULT_ERROR_HANDLING,
    DEFAULT_LOOKAHEAD,
    DEFAULT_SYNC,
    DOMAIN,
    ERROR_IGNORE,
    ERROR_UNAVAILABLE,
)
from .time_parser import TimeParser

_LOGGER = logging.getLogger(__name__)

_TARGET_KEYS = ("entity_id", "area_id", "device_id", "floor_id", "label_id")


def _has_target(options: dict) -> bool:
    """Return True if a valid target is configured (new or old format)."""
    conf = options.get(CONF_TARGET, {})
    # New format: nested "target" dict
    if any(conf.get("target", {}).get(k) for k in _TARGET_KEYS):
        return True
    # Old format: entity_id directly on the target dict
    return bool(conf.get("entity_id"))


def _state_label(state_item: dict, index: int) -> str:
    """Build a human-readable label for a state entry."""
    raw_start = state_item.get(CONF_START) or ""
    raw_end = state_item.get(CONF_END) or ""
    has_start = bool(raw_start)
    has_end = bool(raw_end)
    has_conditions = bool(state_item.get(CONF_CONDITIONS))

    if not has_start and not has_end:
        label = "conditional" if has_conditions else "always"
        return f"#{index + 1} {state_item[CONF_STATE]}: {label}"

    start = raw_start if has_start else "00:00"
    end = raw_end if has_end else "00:00"
    suffix = " (conditional)" if has_conditions else ""
    return f"#{index + 1} {state_item[CONF_STATE]}: {start} \u2192 {end}{suffix}"


# ---------------------------------------------------------------------------
# Shared flow logic (used by both config flow and options flow)
# ---------------------------------------------------------------------------

class _DeclarativeStateFlowMixin:
    """Mixin containing all shared menu/step logic."""

    _pending_options: dict[str, Any]
    _current_state_id: str | None

    # -- Main menu ----------------------------------------------------------

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Main options menu."""
        no_target = " (no target entity)" if not _has_target(self._pending_options) else ""
        return self.async_show_menu(
            step_id="init",
            menu_options={
                "settings": "General Settings",
                "target_settings": "Target Entity",
                "generic_action": f"Generic Action{no_target}",
                "per_state_actions": f"Per-state Actions{no_target}",
                "manage_states": "Manage States",
                "done": "Done",
            },
        )

    # -- General settings ---------------------------------------------------

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure lookahead and error handling."""
        if user_input is not None:
            new_options = dict(self._pending_options)
            new_options[CONF_LOOKAHEAD] = user_input[CONF_LOOKAHEAD]
            new_options[CONF_ERROR_HANDLING] = user_input[CONF_ERROR_HANDLING]
            new_options[CONF_NO_SENSOR] = user_input.get(CONF_NO_SENSOR, False)
            self._pending_options = new_options
            return await self.async_step_init()

        cur = self._pending_options
        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_LOOKAHEAD,
                    default=cur.get(CONF_LOOKAHEAD, DEFAULT_LOOKAHEAD),
                ): NumberSelector(NumberSelectorConfig(min=0, max=10, step=1, mode=NumberSelectorMode.BOX)),
                vol.Required(
                    CONF_ERROR_HANDLING,
                    default=cur.get(CONF_ERROR_HANDLING, DEFAULT_ERROR_HANDLING),
                ): vol.In([ERROR_IGNORE, ERROR_UNAVAILABLE]),
                vol.Optional(
                    CONF_NO_SENSOR,
                    default=cur.get(CONF_NO_SENSOR, False),
                ): cv.boolean,
            }
        )
        return self.async_show_form(step_id="settings", data_schema=data_schema)

    # -- Target settings ----------------------------------------------------

    async def async_step_target_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure target entity control."""
        if user_input is not None:
            ha_target = user_input.get("target") or {}
            sync = user_input.get(CONF_SYNC, DEFAULT_SYNC)
            sync_attr = user_input.get(CONF_SYNC_ATTRIBUTE, "")

            new_options = dict(self._pending_options)
            has_selection = any(ha_target.get(k) for k in _TARGET_KEYS)
            if has_selection:
                existing = new_options.get(CONF_TARGET, {})
                new_target: dict[str, Any] = {"target": ha_target, CONF_SYNC: sync}
                for key in (CONF_ACTION, CONF_DATA, CONF_EXPECTED_STATE,
                            "action_sequence", "actions", "action_expected_states"):
                    if existing.get(key) is not None:
                        new_target[key] = existing[key]
                if sync_attr:
                    new_target[CONF_SYNC_ATTRIBUTE] = sync_attr
                new_options[CONF_TARGET] = new_target
            else:
                new_options.pop(CONF_TARGET, None)

            self._pending_options = new_options
            return await self.async_step_init()

        cur_target = self._pending_options.get(CONF_TARGET, {})
        # Support both new format (nested "target") and old format (entity_id string)
        cur_ha_target = cur_target.get("target") or {}
        if not cur_ha_target and cur_target.get("entity_id"):
            cur_ha_target = {"entity_id": cur_target["entity_id"]}

        data_schema = vol.Schema(
            {
                vol.Optional(
                    "target",
                    description={"suggested_value": cur_ha_target},
                ): TargetSelector(),
                vol.Optional(
                    CONF_SYNC,
                    default=cur_target.get(CONF_SYNC, DEFAULT_SYNC),
                ): cv.boolean,
                vol.Optional(
                    CONF_SYNC_ATTRIBUTE,
                    description={
                        "suggested_value": cur_target.get(CONF_SYNC_ATTRIBUTE, "")
                    },
                ): cv.string,
            }
        )

        return self.async_show_form(
            step_id="target_settings",
            data_schema=data_schema,
        )

    # -- Generic action -----------------------------------------------------

    async def async_step_generic_action(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure the generic (fallback) action for the target entity."""
        if not _has_target(self._pending_options):
            return await self.async_step_init()

        cur_target = self._pending_options.get(CONF_TARGET, {})

        if user_input is not None:
            action_seq = user_input.get("action") or []
            expected = (user_input.get(CONF_EXPECTED_STATE) or "").strip()

            new_options = dict(self._pending_options)
            new_target = dict(cur_target)
            new_target.pop(CONF_ACTION, None)
            new_target.pop(CONF_DATA, None)
            # Strip target/metadata fields — the integration uses the configured target entity,
            # not any entity picked inside the action editor.
            action_seq = [
                {k: v for k, v in a.items() if k not in ("target", "metadata")}
                for a in action_seq
                if isinstance(a, dict)
            ]
            if any(a.get("action") for a in action_seq):
                new_target["action_sequence"] = action_seq
            else:
                new_target.pop("action_sequence", None)
            if expected:
                new_target[CONF_EXPECTED_STATE] = expected
            else:
                new_target.pop(CONF_EXPECTED_STATE, None)
            new_options[CONF_TARGET] = new_target
            self._pending_options = new_options
            return await self.async_step_init()

        if "action_sequence" in cur_target:
            existing_seq = cur_target["action_sequence"]
        elif cur_target.get(CONF_ACTION):
            existing_seq = [
                {"action": cur_target[CONF_ACTION], "data": cur_target.get(CONF_DATA, {})}
            ]
        else:
            existing_seq = []

        data_schema = vol.Schema(
            {
                vol.Optional("action", default=existing_seq): ActionSelector(),
                vol.Optional(
                    CONF_EXPECTED_STATE,
                    description={
                        "suggested_value": cur_target.get(CONF_EXPECTED_STATE, "")
                    },
                ): cv.string,
            }
        )
        return self.async_show_form(
            step_id="generic_action",
            data_schema=data_schema,
        )

    # -- Per-state actions --------------------------------------------------

    async def async_step_per_state_actions(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure per-state action overrides."""
        cur_target = self._pending_options.get(CONF_TARGET, {})
        states_list = self._pending_options.get(CONF_STATES, [])

        state_values: list[str] = list(
            dict.fromkeys(
                s[CONF_STATE]
                for s in states_list
                if s.get(CONF_STATE) and "{{" not in s[CONF_STATE]
            )
        )

        if not _has_target(self._pending_options) or not state_values:
            return await self.async_step_init()

        def _section_key(sv: str) -> str:
            return f"state: {sv}"

        if user_input is not None:
            actions_obj: dict[str, Any] = {}
            expected_states_obj: dict[str, str] = {}
            for state_val in state_values:
                sec = user_input.get(_section_key(state_val)) or {}
                action_seq = sec.get("action") or []
                # Strip target/metadata — target entity comes from target_settings, not the action editor.
                action_seq = [
                    {k: v for k, v in a.items() if k not in ("target", "metadata")}
                    for a in action_seq
                    if isinstance(a, dict)
                ]
                if any(a.get("action") for a in action_seq):
                    actions_obj[state_val] = action_seq
                exp = (sec.get("expected_state") or "").strip()
                if exp:
                    expected_states_obj[state_val] = exp

            new_options = dict(self._pending_options)
            new_target = dict(cur_target)
            if actions_obj:
                new_target["actions"] = actions_obj
            else:
                new_target.pop("actions", None)
            if expected_states_obj:
                new_target["action_expected_states"] = expected_states_obj
            else:
                new_target.pop("action_expected_states", None)
            new_options[CONF_TARGET] = new_target
            self._pending_options = new_options
            return await self.async_step_init()

        cur_actions = cur_target.get("actions", {})
        cur_expected_states = cur_target.get("action_expected_states", {})
        schema_dict: dict[Any, Any] = {}
        for state_val in state_values:
            existing = cur_actions.get(state_val, [])
            if isinstance(existing, dict):
                existing = [existing] if existing else []
            schema_dict[vol.Optional(_section_key(state_val))] = section(
                vol.Schema(
                    {
                        vol.Optional("action", default=existing): ActionSelector(),
                        vol.Optional(
                            "expected_state",
                            description={
                                "suggested_value": cur_expected_states.get(state_val, "")
                            },
                        ): cv.string,
                    }
                )
            )

        return self.async_show_form(
            step_id="per_state_actions",
            data_schema=vol.Schema(schema_dict),
        )

    # -- Manage states menu -------------------------------------------------

    async def async_step_manage_states(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage states menu."""
        count = len(self._pending_options.get(CONF_STATES, []))
        return self.async_show_menu(
            step_id="manage_states",
            menu_options=[
                "add_state",
                "edit_state_select",
                "remove_state_select",
                "move_state_select",
                "back_to_main",
            ],
            description_placeholders={"count": str(count)},
        )

    async def async_step_back_to_main(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Return to main menu."""
        return await self.async_step_init()

    # -- Add state ----------------------------------------------------------

    async def async_step_add_state(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Add a new state."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if user_input.get("cancel"):
                return await self.async_step_manage_states()

            if not user_input.get(CONF_STATE, "").strip():
                errors[CONF_STATE] = "state_required"
            else:
                errors = _validate_time_fields(self.hass, user_input)

            if not errors:
                new_options = dict(self._pending_options)
                states_list = list(new_options.get(CONF_STATES, []))
                states_list.append(_build_state_dict(user_input))
                new_options[CONF_STATES] = states_list
                self._pending_options = new_options
                return await self.async_step_manage_states()

        schema = _state_form_schema().extend(
            {vol.Optional("cancel", default=False): cv.boolean}
        )
        return self.async_show_form(
            step_id="add_state", data_schema=schema, errors=errors
        )

    # -- Edit state ---------------------------------------------------------

    async def async_step_edit_state_select(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Select a state to edit."""
        states_list = self._pending_options.get(CONF_STATES, [])
        if not states_list:
            return await self.async_step_manage_states()

        if user_input is not None:
            if user_input["state_id"] == "__back__":
                return await self.async_step_manage_states()
            self._current_state_id = user_input["state_id"]
            return await self.async_step_edit_state()

        state_choices = {"__back__": "← Back"} | {
            s["id"]: _state_label(s, i) for i, s in enumerate(states_list)
        }
        data_schema = vol.Schema({vol.Required("state_id"): vol.In(state_choices)})
        return self.async_show_form(
            step_id="edit_state_select", data_schema=data_schema
        )

    async def async_step_edit_state(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Edit the selected state."""
        errors: dict[str, str] = {}
        states_list = list(self._pending_options.get(CONF_STATES, []))

        current_state, state_index = _find_state(states_list, self._current_state_id)
        if current_state is None:
            return await self.async_step_manage_states()

        if user_input is not None:
            if user_input.get("cancel"):
                return await self.async_step_manage_states()

            if not user_input.get(CONF_STATE, "").strip():
                errors[CONF_STATE] = "state_required"
            else:
                errors = _validate_time_fields(self.hass, user_input)

            if not errors:
                states_list[state_index] = _build_state_dict(
                    user_input,
                    state_id=current_state["id"],
                )
                new_options = dict(self._pending_options)
                new_options[CONF_STATES] = states_list
                self._pending_options = new_options
                return await self.async_step_manage_states()

        schema = _state_form_schema(defaults=current_state).extend(
            {vol.Optional("cancel", default=False): cv.boolean}
        )
        return self.async_show_form(
            step_id="edit_state", data_schema=schema, errors=errors
        )

    # -- Remove state -------------------------------------------------------

    async def async_step_remove_state_select(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Select a state to remove."""
        states_list = self._pending_options.get(CONF_STATES, [])
        if not states_list:
            return await self.async_step_manage_states()

        if user_input is not None:
            if user_input["state_id"] == "__back__":
                return await self.async_step_manage_states()
            self._current_state_id = user_input["state_id"]
            return await self.async_step_confirm_remove()

        state_choices = {"__back__": "← Back"} | {
            s["id"]: _state_label(s, i) for i, s in enumerate(states_list)
        }
        data_schema = vol.Schema({vol.Required("state_id"): vol.In(state_choices)})
        return self.async_show_form(
            step_id="remove_state_select", data_schema=data_schema
        )

    async def async_step_confirm_remove(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm removal of state."""
        if user_input is not None:
            if user_input.get("confirm"):
                states_list = [
                    s
                    for s in self._pending_options.get(CONF_STATES, [])
                    if s["id"] != self._current_state_id
                ]
                new_options = dict(self._pending_options)
                new_options[CONF_STATES] = states_list
                self._pending_options = new_options
            return await self.async_step_manage_states()

        states_list = self._pending_options.get(CONF_STATES, [])
        current_state, _ = _find_state(states_list, self._current_state_id)
        if current_state is None:
            return await self.async_step_manage_states()

        data_schema = vol.Schema(
            {vol.Required("confirm", default=False): cv.boolean}
        )
        return self.async_show_form(
            step_id="confirm_remove",
            data_schema=data_schema,
            description_placeholders={"state": _state_label(current_state, 0)},
        )

    # -- Move / reorder state -----------------------------------------------

    async def async_step_move_state_select(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Select a state to move."""
        states_list = self._pending_options.get(CONF_STATES, [])
        if len(states_list) < 2:
            return await self.async_step_manage_states()

        if user_input is not None:
            if user_input["state_id"] == "__back__":
                return await self.async_step_manage_states()
            self._current_state_id = user_input["state_id"]
            return await self.async_step_move_state()

        state_choices = {"__back__": "← Back"} | {
            s["id"]: _state_label(s, i) for i, s in enumerate(states_list)
        }
        data_schema = vol.Schema({vol.Required("state_id"): vol.In(state_choices)})
        return self.async_show_form(
            step_id="move_state_select", data_schema=data_schema
        )

    async def async_step_move_state(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Choose where to move the selected state."""
        states_list = list(self._pending_options.get(CONF_STATES, []))
        current_state, state_index = _find_state(states_list, self._current_state_id)
        if current_state is None:
            return await self.async_step_manage_states()

        if user_input is not None:
            direction = user_input["direction"]
            if direction == "cancel":
                return await self.async_step_manage_states()

            new_list = list(states_list)
            new_list.pop(state_index)

            if direction == "top":
                new_list.insert(0, current_state)
            elif direction == "up" and state_index > 0:
                new_list.insert(state_index - 1, current_state)
            elif direction == "down" and state_index < len(states_list) - 1:
                new_list.insert(state_index + 1, current_state)
            elif direction == "bottom":
                new_list.append(current_state)
            else:
                new_list.insert(state_index, current_state)

            new_options = dict(self._pending_options)
            new_options[CONF_STATES] = new_list
            self._pending_options = new_options
            return await self.async_step_manage_states()

        move_options = {}
        if state_index > 0:
            move_options["top"] = "Move to top (highest priority)"
            move_options["up"] = "Move up"
        if state_index < len(states_list) - 1:
            move_options["down"] = "Move down"
            move_options["bottom"] = "Move to bottom (lowest priority)"
        move_options["cancel"] = "← Cancel"

        data_schema = vol.Schema(
            {vol.Required("direction"): vol.In(move_options)}
        )

        return self.async_show_form(
            step_id="move_state",
            data_schema=data_schema,
            description_placeholders={
                "state": _state_label(current_state, state_index)
            },
        )


# ---------------------------------------------------------------------------
# Config flow (initial setup)
# ---------------------------------------------------------------------------

class DeclarativeStateConfigFlow(
    _DeclarativeStateFlowMixin,
    config_entries.ConfigFlow,
    domain=DOMAIN,
):
    """Handle a config flow for Declarative State."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._data: dict[str, Any] = {}
        self._current_state_id: str | None = None
        self._pending_options: dict[str, Any] = {
            CONF_LOOKAHEAD: DEFAULT_LOOKAHEAD,
            CONF_ERROR_HANDLING: DEFAULT_ERROR_HANDLING,
            CONF_NO_SENSOR: False,
            CONF_STATES: [],
        }

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step — collect the sensor name."""
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_NAME].lower())
            self._abort_if_unique_id_configured()
            self._data = {CONF_NAME: user_input[CONF_NAME]}
            return await self.async_step_init()

        data_schema = vol.Schema({vol.Required(CONF_NAME): cv.string})
        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )

    async def async_step_done(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Finish setup — create the config entry."""
        return self.async_create_entry(
            title=self._data[CONF_NAME],
            data=self._data,
            options=self._pending_options,
        )

    async def async_step_import(self, import_config: dict[str, Any]) -> FlowResult:
        """Handle import from YAML configuration."""
        name = import_config[CONF_NAME]
        await self.async_set_unique_id(name.lower())
        self._abort_if_unique_id_configured()

        states_config = import_config.get(CONF_STATES, [])
        states_with_ids = []
        for state_data in states_config:
            state_dict = {
                "id": str(uuid.uuid4()),
                CONF_STATE: state_data[CONF_STATE],
                CONF_START: state_data.get(CONF_START, ""),
                CONF_END: state_data.get(CONF_END, ""),
                CONF_CONDITIONS: state_data.get(CONF_CONDITIONS, []),
            }
            states_with_ids.append(state_dict)

        return self.async_create_entry(
            title=name,
            data={CONF_NAME: name},
            options={
                CONF_LOOKAHEAD: import_config.get(CONF_LOOKAHEAD, DEFAULT_LOOKAHEAD),
                CONF_ERROR_HANDLING: import_config.get(
                    CONF_ERROR_HANDLING, DEFAULT_ERROR_HANDLING
                ),
                CONF_STATES: states_with_ids,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> DeclarativeStateOptionsFlow:
        """Get the options flow for this handler."""
        return DeclarativeStateOptionsFlow(config_entry)


# ---------------------------------------------------------------------------
# Options flow (reconfigure)
# ---------------------------------------------------------------------------

class DeclarativeStateOptionsFlow(
    _DeclarativeStateFlowMixin,
    config_entries.OptionsFlow,
):
    """Handle options flow for Declarative State."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._current_state_id: str | None = None
        # Legacy entries stored config in data instead of options.
        # Fall back to data if options is empty and data has integration config.
        legacy = config_entry.data if (
            not config_entry.options
            and (config_entry.data.get(CONF_STATES) or config_entry.data.get(CONF_LOOKAHEAD))
        ) else {}
        self._pending_options: dict[str, Any] = dict(config_entry.options or legacy)

    async def async_step_done(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Finish options flow."""
        return self.async_create_entry(title="", data=self._pending_options)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_state(
    states_list: list[dict], state_id: str | None
) -> tuple[dict | None, int | None]:
    """Find a state dict by id."""
    for i, s in enumerate(states_list):
        if s["id"] == state_id:
            return s, i
    return None, None


def _validate_time_fields(hass, user_input: dict) -> dict[str, str]:
    """Validate start/end time fields. Returns errors dict."""
    errors: dict[str, str] = {}
    for field in (CONF_START, CONF_END):
        value = user_input.get(field, "")
        if not value:
            continue
        if "{{" in value or "{%" in value:
            continue
        try:
            TimeParser(hass).parse(value)
        except Exception:
            errors[field] = "invalid_time_format"
    return errors


def _build_state_dict(
    user_input: dict,
    *,
    state_id: str | None = None,
) -> dict[str, Any]:
    """Build a state config dict from form input."""
    return {
        "id": state_id or str(uuid.uuid4()),
        CONF_STATE: user_input[CONF_STATE],
        CONF_START: user_input.get(CONF_START) or "",
        CONF_END: user_input.get(CONF_END) or "",
        CONF_CONDITIONS: _conditions_to_ui_format(user_input.get(CONF_CONDITIONS) or []),
    }


def _conditions_to_ui_format(conditions: list[dict]) -> list[dict]:
    """Normalize conditions for storage so the UI can read them back.

    cv.CONDITIONS_SCHEMA makes two changes the ConditionSelector UI can't handle:
    - Converts entity_id strings to single-element lists  → convert back to strings
    - Adds match: "all" to and/or group conditions        → strip it out
    - Converts for: string/dict to timedelta              → convert back to duration dict
    """
    result = []
    for cond in conditions:
        c = dict(cond)
        entity_id = c.get("entity_id")
        if isinstance(entity_id, list) and len(entity_id) == 1:
            c["entity_id"] = entity_id[0]
        # Strip schema-injected "match" field — ConditionSelector doesn't accept it
        c.pop("match", None)
        # Convert for: timedelta/number back to a duration dict the UI can render
        for_value = c.get("for")
        if isinstance(for_value, timedelta):
            total = for_value.total_seconds()
        elif isinstance(for_value, (int, float)):
            total = float(for_value)
        else:
            total = None
        if total is not None:
            h = int(total // 3600)
            m = int((total % 3600) // 60)
            s = total % 60
            c["for"] = {"hours": h, "minutes": m, "seconds": int(s) if s == int(s) else s}
        # Recurse into nested condition lists (and/or/not)
        if "conditions" in c and isinstance(c["conditions"], list):
            c["conditions"] = _conditions_to_ui_format(c["conditions"])
        result.append(c)
    return result


def _state_form_schema(defaults: dict | None = None) -> vol.Schema:
    """Build the shared schema for add/edit state forms."""
    d = defaults or {}
    return vol.Schema(
        {
            vol.Optional(CONF_STATE, default=d.get(CONF_STATE, "")): cv.string,
            vol.Optional(
                CONF_START,
                description={"suggested_value": d.get(CONF_START) or ""},
            ): vol.Any(None, str),
            vol.Optional(
                CONF_END,
                description={"suggested_value": d.get(CONF_END) or ""},
            ): vol.Any(None, str),
            vol.Optional(
                CONF_CONDITIONS,
                default=d.get(CONF_CONDITIONS) or [],
            ): ConditionSelector(),
        }
    )
