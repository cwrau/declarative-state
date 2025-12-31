"""State calculation logic."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.helpers import condition
from homeassistant.util import dt as dt_util

from .const import ERROR_IGNORE, ERROR_UNAVAILABLE
from .exceptions import StateCalculationError
from .models import CalculatedState, StateConfig, StateOccurrence, TimeSpec
from .time_parser import TimeParser

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Default time spec used when only one of start/end is provided
_MIDNIGHT = TimeSpec(raw="00:00", is_template=False, parsed_cron="0 0 * * *")


def _normalize_condition_entity_ids(conditions: list[dict]) -> list[dict]:
    """Recursively normalize entity_id fields to lists (HA requires lists, not strings)."""
    result = []
    for cond in conditions:
        c = dict(cond)
        if "entity_id" in c and isinstance(c["entity_id"], str):
            c["entity_id"] = [c["entity_id"]]
        if "conditions" in c and isinstance(c["conditions"], list):
            c["conditions"] = _normalize_condition_entity_ids(c["conditions"])
        result.append(c)
    return result


class StateCalculator:
    """Calculate current and future states based on configuration."""

    def __init__(
        self,
        hass: HomeAssistant,
        states: list[StateConfig],
        error_handling: str,
        time_parser: TimeParser,
    ):
        """Initialize state calculator."""
        self.hass = hass
        self.states = states
        self.error_handling = error_handling
        self.time_parser = time_parser

    @staticmethod
    def _effective_bounds(
        state_config: StateConfig,
    ) -> tuple[TimeSpec | None, TimeSpec | None]:
        """Return effective (start, end) for a state.

        - Both None  → (None, None)  — truly always-on, caller handles separately.
        - One None   → fill in midnight so the state is day-bounded, not infinite.
        - Both set   → returned as-is.
        """
        start, end = state_config.start, state_config.end
        if start is None and end is None:
            return None, None
        return start or _MIDNIGHT, end or _MIDNIGHT

    async def calculate_states(
        self, lookahead: int, now: datetime | None = None
    ) -> list[CalculatedState]:
        """
        Calculate current and next N states.

        Returns:
            List of CalculatedState with length = lookahead + 1
            Index 0 = current, 1 = next, 2 = 2nd next, etc.
        """
        now = now or dt_util.now()  # Use local time, not UTC
        results = []

        try:
            # Step 1: Evaluate conditions for each state
            valid_states = await self._filter_by_conditions()

            if not valid_states:
                _LOGGER.warning("No states have valid conditions")
                return self._unavailable_results(lookahead + 1)

            # Check if any state has time bounds
            has_time_bounds = any(
                state.start is not None or state.end is not None
                for state in valid_states
            )

            # If no time bounds, return single current state
            if not has_time_bounds:
                # Find last valid state (top-down priority)
                current_state = valid_states[-1]
                result = CalculatedState(
                    occurrence=None,
                    state_value=current_state.state,
                    is_available=True,
                )
                return [result] + self._unavailable_results(lookahead)

            # Step 2: Find current state using get_prev
            current = await self._find_current_state(valid_states, now)

            if not current:
                _LOGGER.debug("No current state found")
                results.append(
                    CalculatedState(
                        occurrence=None, state_value=None, is_available=False
                    )
                )
            else:
                results.append(
                    CalculatedState(
                        occurrence=current, state_value=current.state, is_available=True
                    )
                )

            # Step 3: Find next N state CHANGES using continuous timeline approach
            last_state_value = current.state if current else None
            # For truly always-on states (no start AND no end), current.end is an
            # artificial +365d placeholder — search from now instead.
            # For states with at least one bound (including partial states whose
            # missing side was filled with midnight), current.end is the real end.
            if current and (current.config.start is not None or current.config.end is not None):
                search_from = current.end
            else:
                search_from = now
            next_occurrences = await self._find_next_state_changes(
                valid_states, search_from, lookahead, last_state_value
            )

            # Extend current state to start of first next state (if exists)
            if current and next_occurrences and next_occurrences[0]:
                current.end = next_occurrences[0].start

            for occ in next_occurrences:
                if occ:
                    results.append(
                        CalculatedState(
                            occurrence=occ, state_value=occ.state, is_available=True
                        )
                    )
                else:
                    results.append(
                        CalculatedState(
                            occurrence=None, state_value=None, is_available=False
                        )
                    )

        except Exception as err:
            _LOGGER.error("Error calculating states: %s", err, exc_info=True)

            if self.error_handling == ERROR_UNAVAILABLE:
                return self._unavailable_results(lookahead + 1, err)
            else:
                # ERROR_IGNORE - return what we have or unavailable
                if not results:
                    return self._unavailable_results(lookahead + 1, err)

        return results

    async def _find_current_state(
        self, states: list[StateConfig], now: datetime
    ) -> StateOccurrence | None:
        """Find the current state using get_prev to find most recent start."""
        # Check states in reverse order (last has highest priority)
        for idx in range(len(states) - 1, -1, -1):
            state_config = states[idx]

            try:
                occurrence = await self._get_current_occurrence(state_config, now, idx, states)
                if occurrence:
                    _LOGGER.debug(
                        "Found current state[%d]='%s': %s to %s",
                        idx,
                        state_config.state,
                        occurrence.start,
                        occurrence.end
                    )
                    return occurrence
            except Exception as err:
                _LOGGER.warning(
                    "Error finding current occurrence for state[%d]='%s': %s",
                    idx,
                    state_config.state,
                    err
                )
                if self.error_handling == ERROR_UNAVAILABLE:
                    raise StateCalculationError(
                        f"Failed to find current occurrence: {err}"
                    ) from err

        return None

    async def _get_current_occurrence(
        self, state_config: StateConfig, now: datetime, idx: int, states: list[StateConfig]
    ) -> StateOccurrence | None:
        """Get current occurrence for a single state if it's active now."""
        effective_start, effective_end = self._effective_bounds(state_config)

        if effective_start is None:
            # State without time bounds - always active
            return StateOccurrence(
                state=state_config.state,
                start=now,
                end=now + timedelta(days=365),
                config=state_config,
            )

        # First check if this state is nominally active at the current time
        # (i.e., if we ignored all other states, would this state be active?)
        start_time = await self.time_parser.get_time_at_or_before(effective_start, now)
        if start_time is None:
            return None

        end_time = await self.time_parser.get_time_after(effective_end, start_time)

        # Handle invalid period (start after end) by ignoring start
        if start_time >= end_time:
            _LOGGER.debug(
                "Ignoring start time for state[%d]='%s': start (%s) after end (%s)",
                idx,
                state_config.state,
                start_time,
                end_time,
            )
            start_time = now - timedelta(days=365)

        # Check if we're nominally in this period
        if start_time <= now < end_time:
            # This state is nominally active, but we need to find the TRUE start and end
            # of this specific occurrence considering higher-priority states
            true_start = await self._find_occurrence_start(idx, now, states)
            true_end = await self._find_occurrence_end(idx, true_start, states)

            return StateOccurrence(
                state=state_config.state,
                start=true_start,
                end=true_end,
                config=state_config,
            )

        return None

    async def _find_occurrence_start(
        self, current_idx: int, now: datetime, states: list[StateConfig]
    ) -> datetime:
        """
        Find when the current occurrence of a state actually started.

        This finds the most recent time when a higher-priority state ended or
        when this state started, whichever is more recent.
        """
        current_state_config = states[current_idx]
        effective_start, _ = self._effective_bounds(current_state_config)
        candidates = []

        # Candidate 1: When this state's start expression most recently occurred
        if effective_start:
            start_time = await self.time_parser.get_time_at_or_before(effective_start, now)
            if start_time:
                candidates.append(start_time)

        # Candidate 2: When any higher-priority state most recently ended
        for idx in range(current_idx + 1, len(states)):
            higher_priority_state = states[idx]
            hp_start_spec, hp_end_spec = self._effective_bounds(higher_priority_state)
            if hp_start_spec is None:
                continue

            try:
                # Find the most recent end time of this higher-priority state
                # First find when it most recently started
                hp_start = await self.time_parser.get_time_at_or_before(hp_start_spec, now)
                if hp_start:
                    # Then find when it ended after that start
                    hp_end = await self.time_parser.get_time_after(hp_end_spec, hp_start)
                    # Only consider if the end is before or at now
                    if hp_end <= now:
                        candidates.append(hp_end)
            except Exception:
                continue

        # The occurrence started at the most recent of these events
        # Filter out None values
        valid_candidates = [c for c in candidates if c is not None]
        return max(valid_candidates) if valid_candidates else now

    async def _find_occurrence_end(
        self, current_idx: int, start_time: datetime, states: list[StateConfig]
    ) -> datetime:
        """
        Find when the current occurrence of a state will end.

        This finds the earliest time when this state's end expression occurs or
        when a higher-priority state starts, whichever is earlier.
        """
        current_state_config = states[current_idx]
        _, effective_end = self._effective_bounds(current_state_config)
        candidates = []

        # Candidate 1: When this state's end expression next occurs
        if effective_end:
            try:
                end_time = await self.time_parser.get_time_after(effective_end, start_time)
                candidates.append(end_time)
            except Exception:
                pass

        # Candidate 2: When any higher-priority state next starts
        for idx in range(current_idx + 1, len(states)):
            higher_priority_state = states[idx]
            hp_start_spec, _ = self._effective_bounds(higher_priority_state)
            if hp_start_spec is None:
                continue

            try:
                next_start = await self.time_parser.get_time_after(hp_start_spec, start_time)
                candidates.append(next_start)
            except Exception:
                continue

        # The occurrence ends at the earliest of these events
        # Filter out None values
        valid_candidates = [c for c in candidates if c is not None]
        return min(valid_candidates) if valid_candidates else start_time + timedelta(days=365)

    async def _find_state_value_at_time(
        self, states: list[StateConfig], at_time: datetime
    ) -> str | None:
        """
        Find which state is active at a specific time (just the value, not the full occurrence).

        This is a lightweight version that checks nominal activity without building full occurrences.
        """
        # Check states in reverse order (last has highest priority)
        for idx in range(len(states) - 1, -1, -1):
            state_config = states[idx]
            effective_start, effective_end = self._effective_bounds(state_config)

            # Check if this state is nominally active at at_time
            if effective_start is None:
                # State without time bounds - always active
                return state_config.state

            try:
                # Get the most recent start before at_time
                start_time = await self.time_parser.get_time_at_or_before(effective_start, at_time)
                if start_time is None:
                    continue

                # Get the next end after that start
                end_time = await self.time_parser.get_time_after(effective_end, start_time)

                # Handle invalid period
                if start_time >= end_time:
                    start_time = at_time - timedelta(days=365)

                # Check if at_time is in this nominal period
                if start_time <= at_time < end_time:
                    return state_config.state

            except Exception:
                # Skip states that error
                continue

        return None

    async def _find_true_end_time(
        self, current_idx: int, start_time: datetime, nominal_end_time: datetime, states: list[StateConfig]
    ) -> datetime:
        """
        Find the true end time of a state occurrence.

        The true end time is the EARLIEST of:
        1. The state's own end time (nominal_end_time)
        2. The next start time of any HIGHER-priority state

        Higher priority = higher index in the states list.
        """
        candidates = [nominal_end_time]

        # Check all higher-priority states (indices > current_idx)
        for idx in range(current_idx + 1, len(states)):
            higher_priority_state = states[idx]

            hp_start_spec, _ = self._effective_bounds(higher_priority_state)
            if hp_start_spec is None:
                continue

            try:
                # Find next start time of this higher-priority state after the current start_time
                next_start = await self.time_parser.get_time_after(
                    hp_start_spec, start_time
                )

                # Only consider it if it's before the nominal end time
                if next_start < nominal_end_time:
                    candidates.append(next_start)
                    _LOGGER.debug(
                        "    Higher priority state[%d]='%s' starts at %s, may end current occurrence early",
                        idx,
                        higher_priority_state.state,
                        next_start
                    )
            except Exception as err:
                _LOGGER.debug(
                    "    Could not get next start time for state[%d]='%s': %s",
                    idx,
                    higher_priority_state.state,
                    err
                )
                continue

        true_end = min(candidates)

        if true_end < nominal_end_time:
            _LOGGER.debug(
                "    True end time %s is earlier than nominal end time %s",
                true_end,
                nominal_end_time
            )

        return true_end

    async def _find_next_state_changes(
        self, states: list[StateConfig], now: datetime, count: int, current_state_value: str | None
    ) -> list[StateOccurrence | None]:
        """Find next N state changes using optimized direct search."""
        results = []
        current_start = now
        current_end = now
        current_state = current_state_value

        _LOGGER.debug(
            "Finding next %d state changes from %s (current_state=%s)",
            count,
            current_start,
            current_state_value
        )

        for i in range(count):
            # Step 1: Find next state B with value != current_state starting after current_start
            state_b, state_b_idx = await self._find_next_different_state(
                states, current_start, current_state
            )

            if not state_b:
                _LOGGER.debug("  [%d] No more different states found", i)
                results.append(None)
                continue

            _LOGGER.debug(
                "  [%d] Found candidate state[%d]='%s' at %s",
                i,
                state_b_idx,
                state_b.state,
                state_b.start
            )

            # If state_b starts before current_end, it cuts current short
            if state_b.start < current_end:
                _LOGGER.debug(
                    "    -> Candidate overlaps with current, cutting current short to %s",
                    state_b.start
                )
                current_end = state_b.start

            # Step 2: Check if any higher-priority state with current_state extends past state_b.start
            while True:
                state_c = await self._find_extending_state(
                    states, state_b_idx, current_state, current_end, state_b.start
                )

                if state_c:
                    # Current state extends, update current_end and find new candidate
                    _LOGGER.debug(
                        "    -> Current state '%s' extends until %s",
                        current_state,
                        state_c.end
                    )
                    current_end = state_c.end
                    current_start = current_end

                    # Find next different state after the extension
                    state_b, state_b_idx = await self._find_next_different_state(
                        states, current_start, current_state
                    )

                    if not state_b:
                        _LOGGER.debug("    -> No more different states after extension")
                        results.append(None)
                        break

                    _LOGGER.debug(
                        "    -> New candidate state[%d]='%s' at %s",
                        state_b_idx,
                        state_b.state,
                        state_b.start
                    )
                else:
                    # No extending state, state_b is the true next state
                    _LOGGER.debug(
                        "    -> Confirmed next state '%s' at %s",
                        state_b.state,
                        state_b.start
                    )

                    # Extend previous result to state_b's start
                    if results:
                        results[-1].end = state_b.start

                    results.append(state_b)
                    current_start = state_b.start
                    current_end = state_b.end
                    current_state = state_b.state
                    break

        return results

    async def _find_next_different_state(
        self, states: list[StateConfig], after_time: datetime, exclude_state: str | None
    ) -> tuple[StateOccurrence | None, int | None]:
        """Find the soonest state with a different value starting after the given time.

        Returns: (occurrence, index) tuple
        """
        candidates = []

        # Check if a different state is already active at after_time.
        # This handles always-on (no-start) fallback states, which the cron
        # search below cannot find. Also handles timed states that start
        # exactly at after_time (which get_time_after would push to tomorrow).
        immediate_occ = await self._find_state_at_time(states, after_time)
        if immediate_occ and immediate_occ.state != exclude_state:
            for idx, sc in enumerate(states):
                if sc is immediate_occ.config:
                    candidates.append((immediate_occ, idx))
                    break

        for idx, state_config in enumerate(states):
            # Skip states with the same value as current
            if state_config.state == exclude_state:
                continue

            effective_start, effective_end = self._effective_bounds(state_config)
            if effective_start is None:
                continue  # truly always-on; handled by the immediate check above

            try:
                # Find next occurrence of this state's start
                next_start = await self.time_parser.get_time_after(effective_start, after_time)

                if next_start:
                    next_end = await self.time_parser.get_time_after(effective_end, next_start)

                    if next_end > next_start:
                        # Find true start and end considering overlaps
                        true_start = await self._find_occurrence_start(idx, next_start, states)

                        # Template-based states resolve to current time, which
                        # can produce a true_start in the past when searching
                        # for future occurrences. Fall back to next_start.
                        if true_start < after_time:
                            true_start = next_start

                        true_end = await self._find_occurrence_end(idx, true_start, states)

                        occurrence = StateOccurrence(
                            state=state_config.state,
                            start=true_start,
                            end=true_end,
                            config=state_config,
                        )
                        candidates.append((occurrence, idx))
            except Exception as err:
                _LOGGER.warning(
                    "Error finding next different state for state[%d]='%s': %s",
                    idx,
                    state_config.state,
                    err
                )
                if self.error_handling == ERROR_UNAVAILABLE:
                    raise StateCalculationError(f"Failed to find next state: {err}") from err

        if candidates:
            return min(candidates, key=lambda pair: pair[0].start)

        return None, None

    async def _find_extending_state(
        self,
        states: list[StateConfig],
        after_idx: int,
        state_value: str | None,
        current_end: datetime,
        next_start: datetime,
    ) -> StateOccurrence | None:
        """Find if a higher-priority state extends the current state past next_start.

        Only checks states with index > after_idx (higher priority due to last-defined-wins).
        """
        for idx in range(after_idx + 1, len(states)):
            state_config = states[idx]

            # Only check states with the same value as current
            if state_config.state != state_value:
                continue

            ext_start_spec, ext_end_spec = self._effective_bounds(state_config)
            if ext_start_spec is None:
                continue

            try:
                # Find the occurrence that starts at or before next_start
                start_time = await self.time_parser.get_time_at_or_before(
                    ext_start_spec, next_start
                )

                if not start_time or start_time < current_end:
                    continue

                end_time = await self.time_parser.get_time_after(ext_end_spec, start_time)

                # Check if this occurrence extends past next_start
                if start_time < next_start and end_time > next_start:
                    # Find true start and end
                    true_start = await self._find_occurrence_start(idx, start_time, states)
                    true_end = await self._find_occurrence_end(idx, true_start, states)

                    # Verify extension still exists after finding true boundaries
                    if true_start < next_start and true_end > next_start:
                        return StateOccurrence(
                            state=state_config.state,
                            start=true_start,
                            end=true_end,
                            config=state_config,
                        )
            except Exception as err:
                _LOGGER.warning(
                    "Error finding extending state for state[%d]='%s': %s",
                    idx,
                    state_config.state,
                    err
                )
                if self.error_handling == ERROR_UNAVAILABLE:
                    raise StateCalculationError(f"Failed to find extending state: {err}") from err

        return None

    async def _find_state_at_time(
        self, states: list[StateConfig], at_time: datetime
    ) -> StateOccurrence | None:
        """Find which state is active at a specific time (continuous timeline approach)."""
        # Check states in reverse order (last has highest priority)
        for idx in range(len(states) - 1, -1, -1):
            state_config = states[idx]

            try:
                occurrence = await self._get_occurrence_at_time(state_config, at_time, idx, states)
                if occurrence:
                    _LOGGER.debug(
                        "State at %s is state[%d]='%s': %s to %s",
                        at_time,
                        idx,
                        state_config.state,
                        occurrence.start,
                        occurrence.end
                    )
                    return occurrence
            except Exception as err:
                _LOGGER.warning(
                    "Error finding state at time for state[%d]='%s': %s",
                    idx,
                    state_config.state,
                    err
                )
                if self.error_handling == ERROR_UNAVAILABLE:
                    raise StateCalculationError(
                        f"Failed to find state at time: {err}"
                    ) from err

        return None

    async def _get_occurrence_at_time(
        self, state_config: StateConfig, at_time: datetime, idx: int, states: list[StateConfig]
    ) -> StateOccurrence | None:
        """Get the occurrence that covers a specific time (if any)."""
        effective_start, effective_end = self._effective_bounds(state_config)

        if effective_start is None:
            # State without time bounds - always active
            return StateOccurrence(
                state=state_config.state,
                start=at_time,
                end=at_time + timedelta(days=365),
                config=state_config,
            )

        # First check if this state is nominally active at the given time
        # (i.e., if we ignored all other states, would this state be active?)
        start_time = await self.time_parser.get_time_at_or_before(effective_start, at_time)
        if start_time is None:
            _LOGGER.debug(
                "    state[%d]='%s': no start time at or before %s",
                idx,
                state_config.state,
                at_time
            )
            return None

        end_time = await self.time_parser.get_time_after(effective_end, start_time)

        _LOGGER.debug(
            "    state[%d]='%s': start=%s, end=%s, checking if %s in [%s, %s)",
            idx,
            state_config.state,
            start_time,
            end_time,
            at_time,
            start_time,
            end_time
        )

        # Handle invalid period (start after end) by ignoring start
        if start_time >= end_time:
            _LOGGER.debug(
                "      Ignoring start time: start >= end",
            )
            start_time = at_time - timedelta(days=365)

        # Check if at_time is nominally in this period
        if start_time <= at_time < end_time:
            _LOGGER.debug("      -> ACTIVE at this time")
            # This state is nominally active, but we need to find the TRUE start and end
            # of this specific occurrence considering higher-priority states
            true_start = await self._find_occurrence_start(idx, at_time, states)
            true_end = await self._find_occurrence_end(idx, true_start, states)

            return StateOccurrence(
                state=state_config.state,
                start=true_start,
                end=true_end,
                config=state_config,
            )

        _LOGGER.debug("      -> NOT active at this time")
        return None

    async def _filter_by_conditions(self) -> list[StateConfig]:
        """Filter states by evaluating their conditions."""
        valid = []

        for idx, state_config in enumerate(self.states):
            if not state_config.conditions:
                # No conditions = always valid
                valid.append(state_config)
                continue

            try:
                # Ensure conditions is a list - this is a config error, always fail
                if not isinstance(state_config.conditions, list):
                    raise StateCalculationError(
                        f"Invalid conditions format for state[{idx}]='{state_config.state}': "
                        f"conditions must be a list, got {type(state_config.conditions).__name__}"
                    )

                # Ensure each condition is a dict - this is a config error, always fail
                for cond_idx, cond_item in enumerate(state_config.conditions):
                    if not isinstance(cond_item, dict):
                        raise StateCalculationError(
                            f"Invalid condition format for state[{idx}]='{state_config.state}': "
                            f"condition[{cond_idx}] must be a dict, got {type(cond_item).__name__}: {cond_item!r}"
                        )

                # Normalize entity_id to a list recursively (HA requires lists,
                # but stored conditions may have bare strings, including inside
                # nested and/or/not groups)
                normalized_conditions = _normalize_condition_entity_ids(
                    state_config.conditions
                )

                # All conditions are valid dicts, proceed with evaluation
                # HA conditions - always wrap in AND (works for single or multiple)
                cond_config = {
                    "condition": "and",
                    "conditions": normalized_conditions,
                }

                cond_func = await condition.async_from_config(self.hass, cond_config)

                if cond_func(self.hass, None):
                    valid.append(state_config)

            except Exception as err:
                _LOGGER.warning(
                    "Error evaluating conditions for state[%d]='%s': %s",
                    idx,
                    state_config.state,
                    err,
                )
                if self.error_handling == ERROR_IGNORE:
                    continue
                else:
                    raise StateCalculationError(
                        f"Condition evaluation failed: {err}"
                    ) from err

        return valid

    def _unavailable_results(
        self, count: int, error: Exception | None = None
    ) -> list[CalculatedState]:
        """Create unavailable results."""
        return [
            CalculatedState(
                occurrence=None, state_value=None, is_available=False, error=error
            )
            for _ in range(count)
        ]
