"""Sensor platform for Declarative State."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, TYPE_CHECKING

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.template import Template

from .const import (
    CONF_ACTION,
    CONF_CONDITIONS,
    CONF_DATA,
    CONF_END,
    CONF_ERROR_HANDLING,
    CONF_EXPECTED_STATE,
    CONF_LOOKAHEAD,
    CONF_NO_SENSOR,
    CONF_START,
    CONF_STATE,
    CONF_STATES,
    CONF_SYNC,
    CONF_SYNC_ATTRIBUTE,
    CONF_TARGET,
    DEFAULT_ERROR_HANDLING,
    DEFAULT_LOOKAHEAD,
    DEFAULT_SYNC,
    PLATFORM_SCHEMA,  # noqa: F401 — re-exported for HA platform validation
    SUFFIX_NEXT,
    SUFFIX_TEMPLATE,
)
from .coordinator import DeclarativeStateCoordinator
from .entity_controller import EntityController
from .models import ActionConfig, StateConfig, TargetConfig
from .time_parser import TimeParser

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback, AddConfigEntryEntitiesCallback
    from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

_LOGGER = logging.getLogger(__name__)

# Update interval for sensor state recalculation
UPDATE_INTERVAL = timedelta(minutes=1)


def _parse_for_duration(for_value: Any) -> timedelta | None:
    """Parse a HA 'for' condition value to a timedelta."""
    if isinstance(for_value, timedelta):
        return for_value
    if isinstance(for_value, (int, float)):
        return timedelta(seconds=for_value)
    if isinstance(for_value, dict):
        try:
            return timedelta(**{k: float(v) for k, v in for_value.items()})
        except (TypeError, ValueError):
            return None
    if isinstance(for_value, str):
        try:
            parts = for_value.split(":")
            if len(parts) == 3:
                return timedelta(hours=int(parts[0]), minutes=int(parts[1]), seconds=float(parts[2]))
            if len(parts) == 2:
                return timedelta(hours=int(parts[0]), minutes=int(parts[1]))
        except (ValueError, IndexError):
            return None
    return None


def _extract_for_conditions(
    conditions: list[dict],
) -> list[tuple[list[str], Any, timedelta]]:
    """Recursively extract (entity_ids, required_state, for_duration) from conditions."""
    result = []
    for cond in conditions:
        if not isinstance(cond, dict):
            continue
        if cond.get("condition") == "state" and "for" in cond:
            entity_id = cond.get("entity_id")
            if isinstance(entity_id, str):
                entity_ids = [entity_id]
            elif isinstance(entity_id, list):
                entity_ids = list(entity_id)
            else:
                entity_ids = []
            if entity_ids:
                for_dur = _parse_for_duration(cond["for"])
                if for_dur is not None:
                    result.append((entity_ids, cond.get("state"), for_dur))
        nested = cond.get("conditions")
        if isinstance(nested, list):
            result.extend(_extract_for_conditions(nested))
    return result


def _extract_condition_entities(conditions: list[dict]) -> set[str]:
    """Recursively extract all entity_ids referenced in conditions."""
    entities: set[str] = set()
    for cond in conditions:
        if not isinstance(cond, dict):
            continue
        entity_id = cond.get("entity_id")
        if isinstance(entity_id, str):
            entities.add(entity_id)
        elif isinstance(entity_id, list):
            entities.update(entity_id)
        nested = cond.get("conditions")
        if isinstance(nested, list):
            entities.update(_extract_condition_entities(nested))
    return entities


def _parse_target_config(raw: dict) -> TargetConfig:
    """Parse a validated target config dict into a TargetConfig."""
    # Parse per-state actions
    actions = {}
    # expected_state per state is stored separately to keep the action sequence clean
    action_expected_states = raw.get("action_expected_states", {})
    for state_value, action_raw in raw.get("actions", {}).items():
        # ActionSelector returns a list (action sequence); take the first entry
        if isinstance(action_raw, list):
            if not action_raw:
                continue
            action_raw = action_raw[0]
        if isinstance(action_raw, str):
            action_raw = {CONF_ACTION: action_raw}
        if not isinstance(action_raw, dict) or not action_raw.get(CONF_ACTION):
            continue
        actions[state_value] = ActionConfig(
            action=action_raw[CONF_ACTION],
            data=action_raw.get(CONF_DATA, {}),
            expected_state=action_expected_states.get(state_value) or state_value,
        )
    # Parse generic (fallback) action — UI stores as action_sequence list,
    # YAML stores as plain action string + data dict. Support both.
    if "action_sequence" in raw:
        seq = raw["action_sequence"]
        first = seq[0] if isinstance(seq, list) and seq and isinstance(seq[0], dict) else {}
        default_action = first.get(CONF_ACTION) or None
        default_data = first.get(CONF_DATA, {})
    else:
        default_action = raw.get(CONF_ACTION)
        default_data = raw.get(CONF_DATA, {})

    # Support both new format (target dict) and old format (entity_id string)
    if "target" in raw:
        ha_target = raw["target"]
    else:
        entity_id = raw.get("entity_id")
        ha_target = {"entity_id": [entity_id] if isinstance(entity_id, str) else (entity_id or [])}

    return TargetConfig(
        target=ha_target,
        sync=raw.get(CONF_SYNC, DEFAULT_SYNC),
        actions=actions,
        default_action=default_action,
        default_data=default_data,
        default_expected_state=raw.get(CONF_EXPECTED_STATE) or None,
        sync_attribute=raw.get(CONF_SYNC_ATTRIBUTE),
    )


async def async_setup_platform(
        hass: HomeAssistant,
        config: ConfigType,
        async_add_entities: AddEntitiesCallback,
        discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the Declarative State sensor platform."""
    name = config[CONF_NAME]
    lookahead = config[CONF_LOOKAHEAD]
    error_handling = config[CONF_ERROR_HANDLING]
    states_config = config[CONF_STATES]

    # Parse state configurations
    time_parser = TimeParser(hass)
    states = []

    for state_data in states_config:
        # Helper to convert string to Template if it contains template syntax
        def to_template_if_needed(value):
            if value is None:
                return None
            if isinstance(value, str) and ("{{" in value or "{%" in value):
                template = Template(value, hass)
                return template
            return value

        # Parse start/end times with proper Template objects
        start_value = state_data.get(CONF_START)
        end_value = state_data.get(CONF_END)

        state_config = StateConfig(
            state=state_data[CONF_STATE],
            start=(
                time_parser.parse(to_template_if_needed(start_value))
                if start_value is not None
                else None
            ),
            end=(
                time_parser.parse(to_template_if_needed(end_value))
                if end_value is not None
                else None
            ),
            conditions=state_data.get(CONF_CONDITIONS, []),
            raw_config=state_data,
        )
        states.append(state_config)

    # Create coordinator
    coordinator = DeclarativeStateCoordinator(
        hass=hass,
        name=name,
        states=states,
        error_handling=error_handling,
        lookahead=lookahead,
        update_interval=UPDATE_INTERVAL,
    )

    # Initial data fetch (use async_refresh for YAML platform setup, not async_config_entry_first_refresh)
    await coordinator.async_refresh()

    # Set up event-driven updates for templates and conditions
    tracked_entities = set()

    # Extract entities from templates
    for state in states:
        if state.start and state.start.is_template:
            try:
                info = state.start.raw.async_render_to_info()
                tracked_entities.update(info.entities)
            except Exception as err:
                _LOGGER.warning(
                    "Could not extract entities from start template during setup: %s. "
                    "Will retry on next refresh.",
                    err
                )
        if state.end and state.end.is_template:
            try:
                info = state.end.raw.async_render_to_info()
                tracked_entities.update(info.entities)
            except Exception as err:
                _LOGGER.warning(
                    "Could not extract entities from end template during setup: %s. "
                    "Will retry on next refresh.",
                    err
                )

        # Extract entities from conditions (recursive for nested and/or/not blocks)
        if state.conditions:
            tracked_entities.update(_extract_condition_entities(state.conditions))

    # Build for-condition map: entity_id -> [(required_state, for_duration)]
    entity_for_map: dict[str, list[tuple[Any, timedelta]]] = {}
    for state in states:
        if state.conditions:
            for entity_ids, req_state, for_dur in _extract_for_conditions(state.conditions):
                for eid in entity_ids:
                    entity_for_map.setdefault(eid, []).append((req_state, for_dur))

    # Pending for-timers: delay_seconds -> (cancel_fn, refcount).
    # One timer per unique delay; ref-counted so multiple entities sharing a delay
    # don't create duplicate refreshes.
    pending_timers: dict[float, tuple[Any, int]] = {}
    # Per-entity: which delays are currently owned by this entity's matching state.
    entity_timer_delays: dict[str, list[float]] = {}

    # Set up state change listener for all tracked entities
    @callback
    def entity_state_changed(event):
        """Handle state changes of tracked entities."""
        entity_id = event.data.get("entity_id")
        _LOGGER.debug(
            "Entity %s changed, triggering coordinator refresh for %s",
            entity_id,
            name,
        )
        # Release for-timers owned by this entity
        for delay in entity_timer_delays.pop(entity_id, []):
            if delay in pending_timers:
                cancel_fn, count = pending_timers[delay]
                if count <= 1:
                    cancel_fn()
                    del pending_timers[delay]
                else:
                    pending_timers[delay] = (cancel_fn, count - 1)

        hass.async_create_task(coordinator.async_refresh())

        # Schedule a delayed refresh for each for-condition that now starts ticking
        if entity_id in entity_for_map:
            new_state = event.data.get("new_state")
            if new_state is not None:
                new_state_value = new_state.state
                delays_owned: list[float] = []
                for req_state, for_dur in entity_for_map[entity_id]:
                    state_matches = (
                        req_state is None
                        or (isinstance(req_state, str) and new_state_value == req_state)
                        or (isinstance(req_state, list) and new_state_value in req_state)
                    )
                    if state_matches:
                        # Add a small buffer so HA's last_changed comparison passes cleanly
                        delay = for_dur.total_seconds() + 0.1
                        delays_owned.append(delay)
                        if delay not in pending_timers:
                            _LOGGER.debug(
                                "Scheduling for-condition refresh for %s in %.1fs (entity %s -> %s)",
                                name, delay, entity_id, new_state_value,
                            )
                            def _make_refresh(d: float):
                                def do_refresh(_now):
                                    pending_timers.pop(d, None)
                                    hass.async_create_task(coordinator.async_refresh())
                                return do_refresh
                            pending_timers[delay] = (async_call_later(hass, delay, _make_refresh(delay)), 1)
                        else:
                            cancel_fn, count = pending_timers[delay]
                            pending_timers[delay] = (cancel_fn, count + 1)
                if delays_owned:
                    entity_timer_delays[entity_id] = delays_owned

    if tracked_entities:
        _LOGGER.info(
            "Setting up event-driven updates for %s. Tracking %d entities: %s",
            name,
            len(tracked_entities),
            sorted(tracked_entities)
        )
        async_track_state_change_event(hass, list(tracked_entities), entity_state_changed)
    else:
        _LOGGER.warning(
            "No entities found to track for %s. "
            "Event-driven updates will not work. "
            "Relying on periodic refresh every %s.",
            name,
            UPDATE_INTERVAL
        )

    # Create sensor entities
    entities = []

    # Base sensor
    entities.append(DeclarativeStateSensor(coordinator, name, 0))

    # Next sensors (lookahead state changes)
    if lookahead >= 1:
        entities.append(
            DeclarativeStateSensor(coordinator, f"{name}{SUFFIX_NEXT}", 1)
        )

    for i in range(1, lookahead):
        suffix = SUFFIX_TEMPLATE.format(i)
        entities.append(DeclarativeStateSensor(coordinator, f"{name}{suffix}", i + 1))

    async_add_entities(entities)

    # Set up entity controller if target is configured
    target_raw = config.get(CONF_TARGET)
    if target_raw:
        target_config = _parse_target_config(target_raw)
        controller = EntityController(hass, coordinator, target_config)
        await controller.async_start()
        # Store on coordinator for potential cleanup
        coordinator.entity_controller = controller


async def async_setup_entry(
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Declarative State sensor from a config entry (UI configuration)."""
    # Extract configuration from config entry options.
    # Legacy entries (created before the options-flow refactor) stored their config in
    # config_entry.data instead of config_entry.options — fall back to data when options
    # is empty so those entries continue to work without requiring a manual re-save.
    name = config_entry.title
    _cfg = config_entry.options if config_entry.options else config_entry.data
    lookahead = int(_cfg.get(CONF_LOOKAHEAD, DEFAULT_LOOKAHEAD))
    error_handling = _cfg.get(CONF_ERROR_HANDLING, DEFAULT_ERROR_HANDLING)
    no_sensor = _cfg.get(CONF_NO_SENSOR, False)
    states_config = _cfg.get(CONF_STATES, [])

    # If no states configured yet, don't create sensors but return successfully
    if not states_config:
        _LOGGER.info(
            "No states configured for %s. Add states via Configure to activate the sensor.",
            name
        )
        async_add_entities([])
        return

    # Parse state configurations
    time_parser = TimeParser(hass)
    states = []

    for state_data in states_config:
        # Helper to convert string to Template if it contains template syntax
        def to_template_if_needed(value):
            if value is None:
                return None
            if isinstance(value, str) and ("{{" in value or "{%" in value):
                template = Template(value, hass)
                return template
            return value

        # Parse start/end times with proper Template objects
        start_value = state_data.get(CONF_START) or None  # treat "" as None
        end_value = state_data.get(CONF_END) or None

        # Note: Config entry states have an "id" field that we ignore during parsing
        state_config = StateConfig(
            state=state_data[CONF_STATE],
            start=(
                time_parser.parse(to_template_if_needed(start_value))
                if start_value is not None
                else None
            ),
            end=(
                time_parser.parse(to_template_if_needed(end_value))
                if end_value is not None
                else None
            ),
            conditions=state_data.get(CONF_CONDITIONS, []),
            raw_config=state_data,
        )
        states.append(state_config)

    # Create coordinator
    coordinator = DeclarativeStateCoordinator(
        hass=hass,
        name=name,
        states=states,
        error_handling=error_handling,
        lookahead=lookahead,
        update_interval=UPDATE_INTERVAL,
    )

    # Initial data fetch
    await coordinator.async_config_entry_first_refresh()

    # Set up event-driven updates for templates and conditions
    tracked_entities = set()

    # Extract entities from templates
    for state in states:
        if state.start and state.start.is_template:
            try:
                info = state.start.raw.async_render_to_info()
                tracked_entities.update(info.entities)
            except Exception as err:
                _LOGGER.warning(
                    "Could not extract entities from start template during setup: %s. "
                    "Will retry on next refresh.",
                    err
                )
        if state.end and state.end.is_template:
            try:
                info = state.end.raw.async_render_to_info()
                tracked_entities.update(info.entities)
            except Exception as err:
                _LOGGER.warning(
                    "Could not extract entities from end template during setup: %s. "
                    "Will retry on next refresh.",
                    err
                )

        # Extract entities from conditions (recursive for nested and/or/not blocks)
        if state.conditions:
            tracked_entities.update(_extract_condition_entities(state.conditions))

    # Build for-condition map: entity_id -> [(required_state, for_duration)]
    entity_for_map: dict[str, list[tuple[Any, timedelta]]] = {}
    for state in states:
        if state.conditions:
            for entity_ids, req_state, for_dur in _extract_for_conditions(state.conditions):
                for eid in entity_ids:
                    entity_for_map.setdefault(eid, []).append((req_state, for_dur))

    # Pending for-timers: delay_seconds -> (cancel_fn, refcount).
    # One timer per unique delay; ref-counted so multiple entities sharing a delay
    # don't create duplicate refreshes.
    pending_timers: dict[float, tuple[Any, int]] = {}
    # Per-entity: which delays are currently owned by this entity's matching state.
    entity_timer_delays: dict[str, list[float]] = {}

    # Set up state change listener for all tracked entities
    @callback
    def entity_state_changed(event):
        """Handle state changes of tracked entities."""
        entity_id = event.data.get("entity_id")
        _LOGGER.debug(
            "Entity %s changed, triggering coordinator refresh for %s",
            entity_id,
            name,
        )
        # Release for-timers owned by this entity
        for delay in entity_timer_delays.pop(entity_id, []):
            if delay in pending_timers:
                cancel_fn, count = pending_timers[delay]
                if count <= 1:
                    cancel_fn()
                    del pending_timers[delay]
                else:
                    pending_timers[delay] = (cancel_fn, count - 1)

        hass.async_create_task(coordinator.async_refresh())

        # Schedule a delayed refresh for each for-condition that now starts ticking
        if entity_id in entity_for_map:
            new_state = event.data.get("new_state")
            if new_state is not None:
                new_state_value = new_state.state
                delays_owned: list[float] = []
                for req_state, for_dur in entity_for_map[entity_id]:
                    state_matches = (
                        req_state is None
                        or (isinstance(req_state, str) and new_state_value == req_state)
                        or (isinstance(req_state, list) and new_state_value in req_state)
                    )
                    if state_matches:
                        # Add a small buffer so HA's last_changed comparison passes cleanly
                        delay = for_dur.total_seconds() + 0.1
                        delays_owned.append(delay)
                        if delay not in pending_timers:
                            _LOGGER.debug(
                                "Scheduling for-condition refresh for %s in %.1fs (entity %s -> %s)",
                                name, delay, entity_id, new_state_value,
                            )
                            def _make_refresh(d: float):
                                def do_refresh(_now):
                                    pending_timers.pop(d, None)
                                    hass.async_create_task(coordinator.async_refresh())
                                return do_refresh
                            pending_timers[delay] = (async_call_later(hass, delay, _make_refresh(delay)), 1)
                        else:
                            cancel_fn, count = pending_timers[delay]
                            pending_timers[delay] = (cancel_fn, count + 1)
                if delays_owned:
                    entity_timer_delays[entity_id] = delays_owned

    if tracked_entities:
        _LOGGER.info(
            "Setting up event-driven updates for %s. Tracking %d entities: %s",
            name,
            len(tracked_entities),
            sorted(tracked_entities)
        )
        async_track_state_change_event(hass, list(tracked_entities), entity_state_changed)
    else:
        _LOGGER.warning(
            "No entities found to track for %s. "
            "Event-driven updates will not work. "
            "Relying on periodic refresh every %s.",
            name,
            UPDATE_INTERVAL
        )

    # Create sensor entities (skip if no_sensor mode is enabled)
    if no_sensor:
        async_add_entities([])
    else:
        entities = []

        # Base sensor - use config entry ID for unique_id base
        entities.append(
            DeclarativeStateSensor(
                coordinator, name, 0, unique_id_base=config_entry.entry_id
            )
        )

        # Next sensors (lookahead state changes)
        if lookahead >= 1:
            entities.append(
                DeclarativeStateSensor(
                    coordinator,
                    f"{name}{SUFFIX_NEXT}",
                    1,
                    unique_id_base=config_entry.entry_id,
                )
            )

        for i in range(1, lookahead):
            suffix = SUFFIX_TEMPLATE.format(i)
            entities.append(
                DeclarativeStateSensor(
                    coordinator,
                    f"{name}{suffix}",
                    i + 1,
                    unique_id_base=config_entry.entry_id,
                )
            )

        async_add_entities(entities)

    # Set up entity controller if target is configured
    target_raw = _cfg.get(CONF_TARGET)
    if target_raw:
        from .const import DOMAIN
        target_config = _parse_target_config(target_raw)
        controller = EntityController(hass, coordinator, target_config)
        await controller.async_start()
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN].setdefault(config_entry.entry_id, {})
        hass.data[DOMAIN][config_entry.entry_id]["controller"] = controller


class DeclarativeStateSensor(SensorEntity, RestoreEntity):
    """Declarative state sensor."""

    _attr_should_poll = False

    def __init__(
            self,
            coordinator: DeclarativeStateCoordinator,
            name: str,
            index: int,
            unique_id_base: str | None = None,
    ) -> None:
        """Initialize the sensor."""
        self.coordinator = coordinator
        self._attr_name = name
        # Use provided unique_id_base (from config entry) or fallback to coordinator name (YAML)
        base = unique_id_base if unique_id_base is not None else coordinator.name
        self._attr_unique_id = f"{base}_{index}"
        self._index = index
        self._attr_native_value = None
        self._attr_extra_state_attributes = {}

    async def async_added_to_hass(self) -> None:
        """Handle entity added to hass."""
        await super().async_added_to_hass()

        # Restore state
        if (last_state := await self.async_get_last_state()) is not None:
            self._attr_native_value = last_state.state

        # Subscribe to coordinator updates
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_coordinator_update)
        )

        # Trigger initial update
        self._handle_coordinator_update()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self._index < len(self.coordinator.data):
            calculated_state = self.coordinator.data[self._index]

            self._attr_native_value = calculated_state.state_value
            self._attr_available = calculated_state.is_available
            self._attr_extra_state_attributes = calculated_state.get_attributes()
        else:
            self._attr_available = False
            self._attr_native_value = None
            self._attr_extra_state_attributes = {}

        self.async_write_ha_state()

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        return self._attr_native_value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        return self._attr_extra_state_attributes or {}
