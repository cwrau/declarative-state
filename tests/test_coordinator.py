"""Tests for coordinator module."""
import pytest
from datetime import timedelta
from unittest.mock import MagicMock, AsyncMock, patch

from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from custom_components.declarative_state.coordinator import DeclarativeStateCoordinator
from custom_components.declarative_state.models import (
    StateConfig,
    TimeSpec,
    StateOccurrence,
    CalculatedState,
)
from custom_components.declarative_state.const import ERROR_IGNORE


class TestDeclarativeStateCoordinator:
    """Test DeclarativeStateCoordinator class."""

    @pytest.mark.asyncio
    @patch("homeassistant.helpers.frame.report_usage")
    async def test_coordinator_initialization(self, mock_report_usage):
        """Test that coordinator initializes with correct parameters."""
        mock_hass = MagicMock()
        mock_hass.config.time_zone = "Europe/Berlin"

        states = [
            StateConfig(
                state="on",
                start=TimeSpec(raw="16:00", is_template=False, parsed_cron="0 16 * * *"),
                end=TimeSpec(raw="22:00", is_template=False, parsed_cron="0 22 * * *"),
                conditions=[],
                raw_config={},
            ),
        ]

        coordinator = DeclarativeStateCoordinator(
            hass=mock_hass,
            name="test_coordinator",
            states=states,
            error_handling=ERROR_IGNORE,
            lookahead=2,
            update_interval=timedelta(minutes=5),
        )

        # Verify coordinator attributes
        assert coordinator.name == "test_coordinator"
        assert coordinator.states == states
        assert coordinator.error_handling == ERROR_IGNORE
        assert coordinator.lookahead == 2
        assert coordinator.update_interval == timedelta(minutes=5)

        # Verify calculator was initialized
        assert coordinator.calculator is not None
        assert coordinator.calculator.states == states
        assert coordinator.calculator.error_handling == ERROR_IGNORE

    @pytest.mark.asyncio
    @patch("homeassistant.helpers.frame.report_usage")
    async def test_coordinator_update_success(self, mock_report_usage):
        """Test coordinator update with successful state calculation."""
        mock_hass = MagicMock()
        mock_hass.config.time_zone = "Europe/Berlin"

        states = [
            StateConfig(
                state="on",
                start=TimeSpec(raw="16:00", is_template=False, parsed_cron="0 16 * * *"),
                end=TimeSpec(raw="22:00", is_template=False, parsed_cron="0 22 * * *"),
                conditions=[],
                raw_config={},
            ),
        ]

        coordinator = DeclarativeStateCoordinator(
            hass=mock_hass,
            name="test_coordinator",
            states=states,
            error_handling=ERROR_IGNORE,
            lookahead=2,
            update_interval=timedelta(minutes=5),
        )

        # Mock successful calculation
        now = dt_util.parse_datetime("2025-12-31T18:00:00+01:00")
        state_config = states[0]
        occurrence = StateOccurrence(
            state="on",
            start=now.replace(hour=16, minute=0),
            end=now.replace(hour=22, minute=0),
            config=state_config,
        )
        expected_result = [
            CalculatedState(
                occurrence=occurrence,
                state_value="on",
                is_available=True,
            ),
        ]

        # Patch the calculator's calculate_states method
        with patch.object(
            coordinator.calculator,
            "calculate_states",
            new_callable=AsyncMock,
            return_value=expected_result,
        ):
            result = await coordinator._async_update_data()

            # Verify result
            assert result == expected_result
            assert len(result) == 1
            assert result[0].state_value == "on"
            assert result[0].is_available is True

            # Verify calculator was called with correct lookahead
            coordinator.calculator.calculate_states.assert_called_once_with(2)

    @pytest.mark.asyncio
    @patch("homeassistant.helpers.frame.report_usage")
    async def test_coordinator_update_failure(self, mock_report_usage):
        """Test coordinator update when calculation raises exception."""
        mock_hass = MagicMock()
        mock_hass.config.time_zone = "Europe/Berlin"

        states = [
            StateConfig(
                state="on",
                start=TimeSpec(raw="16:00", is_template=False, parsed_cron="0 16 * * *"),
                end=TimeSpec(raw="22:00", is_template=False, parsed_cron="0 22 * * *"),
                conditions=[],
                raw_config={},
            ),
        ]

        coordinator = DeclarativeStateCoordinator(
            hass=mock_hass,
            name="test_coordinator",
            states=states,
            error_handling=ERROR_IGNORE,
            lookahead=2,
            update_interval=timedelta(minutes=5),
        )

        # Mock calculator raising exception
        test_exception = ValueError("Test calculation error")

        with patch.object(
            coordinator.calculator,
            "calculate_states",
            new_callable=AsyncMock,
            side_effect=test_exception,
        ):
            # Coordinator should wrap exception in UpdateFailed
            with pytest.raises(UpdateFailed) as exc_info:
                await coordinator._async_update_data()

            # Verify error message
            assert "Error calculating states" in str(exc_info.value)
            assert "Test calculation error" in str(exc_info.value)

    @pytest.mark.asyncio
    @patch("homeassistant.helpers.frame.report_usage")
    async def test_coordinator_update_unavailable(self, mock_report_usage):
        """Test coordinator update when state is unavailable."""
        mock_hass = MagicMock()
        mock_hass.config.time_zone = "Europe/Berlin"

        states = [
            StateConfig(
                state="on",
                start=TimeSpec(raw="16:00", is_template=False, parsed_cron="0 16 * * *"),
                end=TimeSpec(raw="22:00", is_template=False, parsed_cron="0 22 * * *"),
                conditions=[],
                raw_config={},
            ),
        ]

        coordinator = DeclarativeStateCoordinator(
            hass=mock_hass,
            name="test_coordinator",
            states=states,
            error_handling=ERROR_IGNORE,
            lookahead=2,
            update_interval=timedelta(minutes=5),
        )

        # Mock unavailable state result
        unavailable_result = [
            CalculatedState(
                occurrence=None,
                state_value=None,
                is_available=False,
            ),
        ]

        with patch.object(
            coordinator.calculator,
            "calculate_states",
            new_callable=AsyncMock,
            return_value=unavailable_result,
        ):
            result = await coordinator._async_update_data()

            # Verify unavailable result is returned correctly
            assert result == unavailable_result
            assert len(result) == 1
            assert result[0].state_value is None
            assert result[0].is_available is False
            assert result[0].occurrence is None
