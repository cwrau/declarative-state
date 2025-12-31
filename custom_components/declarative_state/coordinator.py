"""Data coordinator for Declarative State."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .models import CalculatedState, StateConfig
from .state_calculator import StateCalculator
from .time_parser import TimeParser

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class DeclarativeStateCoordinator(DataUpdateCoordinator[list[CalculatedState]]):
    """Coordinator to manage state calculations."""

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        states: list[StateConfig],
        error_handling: str,
        lookahead: int,
        update_interval: timedelta,
    ) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=name,
            update_interval=update_interval,
        )

        self.states = states
        self.error_handling = error_handling
        self.lookahead = lookahead

        # Initialize calculator
        time_parser = TimeParser(hass)
        self.calculator = StateCalculator(
            hass=hass,
            states=states,
            error_handling=error_handling,
            time_parser=time_parser,
        )

    async def _async_update_data(self) -> list[CalculatedState]:
        """Fetch data from calculator."""
        try:
            results = await self.calculator.calculate_states(self.lookahead)

            # Log the calculated states for debugging
            _LOGGER.debug(
                "Coordinator %s calculated %d states: %s",
                self.name,
                len(results),
                [
                    f"[{i}] {r.state_value if r.is_available else 'unavailable'}"
                    for i, r in enumerate(results)
                ]
            )

            return results
        except Exception as err:
            raise UpdateFailed(f"Error calculating states: {err}") from err
