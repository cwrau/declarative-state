"""Data models for Declarative State."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util


@dataclass
class TimeSpec:
    """Time specification that can be a template or a parsed time."""

    raw: str | Template
    is_template: bool
    parsed_cron: str | None = None  # Cron expression if not template


@dataclass
class StateConfig:
    """Configuration for a single state."""

    state: str
    start: TimeSpec | None = None
    end: TimeSpec | None = None
    conditions: list[dict[str, Any]] = field(default_factory=list)
    raw_config: dict[str, Any] = field(default_factory=dict)


@dataclass
class StateOccurrence:
    """A specific occurrence of a state with resolved times."""

    state: str
    start: datetime
    end: datetime
    config: StateConfig

    @property
    def is_current(self) -> bool:
        """Check if this occurrence covers current time."""
        now = dt_util.now()
        return self.start <= now < self.end


@dataclass
class ActionConfig:
    """Configuration for an action to perform when entering a state."""

    action: str  # "domain.service" e.g. "switch.turn_on"
    data: dict[str, Any] = field(default_factory=dict)
    expected_state: str | None = None  # What the target entity should report


@dataclass
class TargetConfig:
    """Configuration for controlling an external entity."""

    target: dict  # HA target dict: {entity_id, area_id, device_id, label_id, floor_id}
    sync: bool = True
    actions: dict[str, ActionConfig] = field(default_factory=dict)  # state_value -> ActionConfig
    default_action: str | None = None  # generic fallback "domain.service"
    default_data: dict[str, Any] = field(default_factory=dict)  # may contain {{ state }} templates
    default_expected_state: str | None = None  # what the target reports after the generic action
    sync_attribute: str | None = None  # entity attribute to monitor for generic action drift


@dataclass
class CalculatedState:
    """Result of state calculation."""

    occurrence: StateOccurrence | None
    state_value: str | None
    is_available: bool
    error: Exception | None = None

    def get_attributes(self) -> dict[str, Any]:
        """Get sensor attributes from occurrence."""
        if not self.occurrence:
            return {}

        # Times are already in the correct timezone from time_parser
        # Don't convert again to avoid timezone issues
        attrs = {
            "start": self.occurrence.start.isoformat(),
            "end": self.occurrence.end.isoformat(),
        }

        # Add time-only attributes if times are today
        now = dt_util.now()
        if self.occurrence.start.date() == now.date():
            attrs["start_time"] = self.occurrence.start.strftime("%H:%M")
        if self.occurrence.end.date() == now.date():
            attrs["end_time"] = self.occurrence.end.strftime("%H:%M")

        return attrs
