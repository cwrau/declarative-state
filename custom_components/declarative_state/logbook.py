"""Logbook support for Declarative State."""
from __future__ import annotations

from homeassistant.components.logbook import LOGBOOK_ENTRY_MESSAGE, LOGBOOK_ENTRY_NAME
from homeassistant.core import Event, callback

from .const import DOMAIN
from .entity_controller import EVENT_ACTION_APPLIED


@callback
def async_describe_events(hass, async_describe_event):
    """Describe Declarative State events for the logbook."""

    @callback
    def describe_action_event(event: Event):
        """Describe a declarative_state_action event."""
        data = event.data
        reason = data.get("reason", "state_change")
        sensor = data.get("sensor", "")

        # HA prepends the state change (e.g. "turned on triggered by ...")
        # so keep our part short: just the sensor name + reason
        if reason == "drift_correction":
            message = f"({sensor}, drift correction)" if sensor else "(drift correction)"
        else:
            message = f"({sensor})" if sensor else ""

        return {
            LOGBOOK_ENTRY_NAME: "Declarative State",
            LOGBOOK_ENTRY_MESSAGE: message,
        }

    async_describe_event(DOMAIN, EVENT_ACTION_APPLIED, describe_action_event)
