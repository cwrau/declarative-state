"""Entity controller for Declarative State."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Callable

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import Context, CoreState, callback
from homeassistant.helpers.area_registry import EVENT_AREA_REGISTRY_UPDATED
from homeassistant.helpers.device_registry import EVENT_DEVICE_REGISTRY_UPDATED
from homeassistant.helpers.entity_registry import EVENT_ENTITY_REGISTRY_UPDATED
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.label_registry import EVENT_LABEL_REGISTRY_UPDATED
from homeassistant.helpers.target import TargetSelectorData, async_extract_referenced_entity_ids
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .models import TargetConfig

EVENT_ACTION_APPLIED = f"{DOMAIN}_action"

if TYPE_CHECKING:
    from homeassistant.core import Event, HomeAssistant

    from .coordinator import DeclarativeStateCoordinator

_LOGGER = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 2.0

_REGISTRY_EVENTS = (
    EVENT_ENTITY_REGISTRY_UPDATED,
    EVENT_AREA_REGISTRY_UPDATED,
    EVENT_DEVICE_REGISTRY_UPDATED,
    EVENT_LABEL_REGISTRY_UPDATED,
)


class EntityController:
    """Controls external HA entities based on calculated state changes."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: DeclarativeStateCoordinator,
        target: TargetConfig,
    ) -> None:
        """Initialize entity controller."""
        self.hass = hass
        self.coordinator = coordinator
        self.target = target
        self._last_applied_state: str | None = None
        self._last_action_time: datetime | None = None
        self._unsub_coordinator: Callable | None = None
        self._unsub_state_change: Callable | None = None
        self._unsub_registry: list[Callable] = []
        self._tracked_entities: frozenset[str] = frozenset()

    def _resolve_entities(self) -> frozenset[str]:
        """Resolve the target to a set of entity IDs."""
        selector_data = TargetSelectorData(self.target.target)
        selected = async_extract_referenced_entity_ids(self.hass, selector_data)
        return frozenset(selected.referenced | selected.indirectly_referenced)

    def _update_entity_tracking(self) -> None:
        """Re-resolve target entities and update state-change subscriptions."""
        new_entities = self._resolve_entities()
        if new_entities == self._tracked_entities:
            return

        self._tracked_entities = new_entities

        if self._unsub_state_change:
            self._unsub_state_change()
            self._unsub_state_change = None

        if new_entities:
            self._unsub_state_change = async_track_state_change_event(
                self.hass,
                list(new_entities),
                self._handle_target_state_change,
            )
            _LOGGER.debug(
                "Tracking %d entities for drift detection: %s",
                len(new_entities),
                sorted(new_entities),
            )

    async def async_start(self) -> None:
        """Start controlling the target entities."""
        self._unsub_coordinator = self.coordinator.async_add_listener(
            self._handle_coordinator_update
        )

        if self.target.sync:
            self._update_entity_tracking()

            @callback
            def _on_registry_updated(_event: Event) -> None:
                self._update_entity_tracking()

            for event_type in _REGISTRY_EVENTS:
                self._unsub_registry.append(
                    self.hass.bus.async_listen(event_type, _on_registry_updated)
                )

        if self.hass.state is CoreState.running:
            self._handle_coordinator_update()
        else:
            @callback
            def _on_ha_started(_event: Event) -> None:
                self._handle_coordinator_update()

            self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED, _on_ha_started
            )

    async def async_stop(self) -> None:
        """Stop controlling the target entities."""
        if self._unsub_coordinator:
            self._unsub_coordinator()
            self._unsub_coordinator = None
        if self._unsub_state_change:
            self._unsub_state_change()
            self._unsub_state_change = None
        for unsub in self._unsub_registry:
            unsub()
        self._unsub_registry.clear()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle coordinator state recalculation."""
        if not self.coordinator.data:
            return

        current = self.coordinator.data[0]
        if not current.is_available or current.state_value is None:
            return

        if current.state_value != self._last_applied_state:
            self.hass.async_create_task(self._apply_action(current.state_value))

    @callback
    def _handle_target_state_change(self, event: Event) -> None:
        """Handle target entity state change (drift detection)."""
        if self._last_action_time:
            elapsed = (dt_util.utcnow() - self._last_action_time).total_seconds()
            if elapsed < DEBOUNCE_SECONDS:
                return

        if self._last_applied_state is None:
            return

        new_state = event.data.get("new_state")
        if not new_state:
            return

        entity_id = event.data.get("entity_id")

        per_state = self.target.actions.get(self._last_applied_state)
        if per_state:
            expected = per_state.expected_state or self._last_applied_state
            if new_state.state != expected:
                self._trigger_drift_correction(entity_id, new_state.state, expected)
            return

        if self.target.default_action:
            expected = self.target.default_expected_state or self._last_applied_state
            if self.target.sync_attribute:
                actual = new_state.attributes.get(self.target.sync_attribute)
            else:
                actual = new_state.state
            if actual is not None and not self._values_match(actual, expected):
                self._trigger_drift_correction(entity_id, actual, expected)

    def _trigger_drift_correction(self, entity_id: str, actual, expected) -> None:
        """Log and schedule a drift correction."""
        _LOGGER.info(
            "Target %s drifted to '%s', expected '%s'. Re-applying action for state '%s'",
            entity_id,
            actual,
            expected,
            self._last_applied_state,
        )
        self.hass.async_create_task(
            self._apply_action(self._last_applied_state, reason="drift_correction")
        )

    @staticmethod
    def _values_match(actual, expected_str: str) -> bool:
        """Compare values with numeric awareness (200.0 == '200')."""
        try:
            return float(actual) == float(expected_str)
        except (ValueError, TypeError):
            return str(actual) == expected_str

    async def _apply_action(
        self, state_value: str, *, reason: str = "state_change"
    ) -> None:
        """Execute the service call for a given state value."""
        per_state = self.target.actions.get(state_value)

        if per_state:
            action_str = per_state.action
            service_data = per_state.data or None
        elif self.target.default_action:
            action_str = self.target.default_action
            service_data = self._render_data(self.target.default_data, state_value)
        else:
            _LOGGER.debug(
                "No action for state '%s' on target %s",
                state_value,
                self.target.target,
            )
            self._last_applied_state = state_value
            return

        domain, service = action_str.split(".", 1)

        _LOGGER.info(
            "Applying %s.%s to %s for state '%s' (reason: %s)",
            domain,
            service,
            self.target.target,
            state_value,
            reason,
        )

        self._last_action_time = dt_util.utcnow()
        self._last_applied_state = state_value

        context = Context()

        self.hass.bus.async_fire(
            EVENT_ACTION_APPLIED,
            {
                "target": self.target.target,
                "sensor": self.coordinator.name,
                "state": state_value,
                "action": action_str,
                "reason": reason,
            },
            context=context,
        )

        try:
            await self.hass.services.async_call(
                domain,
                service,
                service_data=service_data,
                target=self.target.target,
                context=context,
                blocking=True,
            )
        except Exception:
            _LOGGER.exception(
                "Failed to call %s.%s for target %s",
                domain,
                service,
                self.target.target,
            )

    def _render_data(self, data: dict, state_value: str) -> dict | None:
        """Render template strings in data dict, substituting {{ state }}."""
        if not data:
            return None
        rendered = {}
        for key, value in data.items():
            if isinstance(value, str) and ("{{" in value or "{%" in value):
                tpl = Template(value, self.hass)
                tpl.hass = self.hass
                rendered[key] = tpl.async_render({"state": state_value})
            else:
                rendered[key] = value
        return rendered
