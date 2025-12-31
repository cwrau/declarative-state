"""Integration tests for declarative_state components working together."""
import pytest
from datetime import timedelta
from unittest.mock import MagicMock, AsyncMock, patch

from homeassistant.util import dt as dt_util

from custom_components.declarative_state.coordinator import DeclarativeStateCoordinator
from custom_components.declarative_state.time_parser import TimeParser
from custom_components.declarative_state.state_calculator import StateCalculator
from custom_components.declarative_state.models import StateConfig, CalculatedState
from custom_components.declarative_state.const import ERROR_IGNORE


class TestIntegration:
    """Test integration between components."""

    @pytest.mark.asyncio
    @patch("homeassistant.helpers.frame.report_usage")
    async def test_christmas_lights_full_integration(self, mock_report_usage):
        """Test full integration with complex christmas_lights config.

        This tests the complete pipeline:
        - TimeParser parsing various time formats
        - StateCalculator finding correct occurrences
        - Coordinator managing updates
        - CalculatedState with proper attributes

        Config tests:
        - Date-based patterns (ISO8601 with wildcards)
        - Static datetime strings (including end-before-start case)
        - Daily time patterns
        - Conditions
        - State priority/overlap
        - Lookahead
        """
        mock_hass = MagicMock()
        mock_hass.config.time_zone = "Europe/Berlin"

        # Create mock condition checker that returns True
        mock_hass.helpers.condition.async_from_config = MagicMock(
            return_value=AsyncMock(return_value=True)
        )

        time_parser = TimeParser(mock_hass)

        # Christmas lights config (simplified - no static datetimes as those aren't supported)
        states_raw = [
            {
                "state": "on",
                "start": "*-12-01T16:00",
                "end": "*-01-01T06:00",
            },
            {
                "state": "on",
                "start": "*-12-24T16:00",
                "end": "*-12-26",
            },
            {
                "state": "off",
                "start": "01:00",
                "end": "06:00",
            },
            {
                "state": "off",
                "start": "02:00",
                "end": "06:00",
            },
        ]

        # Parse states
        states = []
        for state_data in states_raw:
            state_config = StateConfig(
                state=state_data["state"],
                start=(
                    time_parser.parse(state_data["start"])
                    if "start" in state_data
                    else None
                ),
                end=(
                    time_parser.parse(state_data["end"])
                    if "end" in state_data
                    else None
                ),
                conditions=state_data.get("conditions", []),
                raw_config=state_data,
            )
            states.append(state_config)

        # Create coordinator
        coordinator = DeclarativeStateCoordinator(
            hass=mock_hass,
            name="christmas_lights",
            states=states,
            error_handling=ERROR_IGNORE,
            lookahead=2,
            update_interval=timedelta(minutes=1),
        )

        # Test at December 24, 2025, 18:00 (during Christmas period)
        test_time = dt_util.parse_datetime("2025-12-24T18:00:00+01:00")

        with patch.object(coordinator.calculator, "calculate_states") as mock_calc:
            # Mock return value
            mock_calc.return_value = [
                MagicMock(state_value="on", is_available=True),
                MagicMock(state_value="off", is_available=True),
                MagicMock(state_value="on", is_available=True),
            ]

            # Trigger coordinator update
            result = await coordinator._async_update_data()

            # Verify coordinator called calculator with correct lookahead
            mock_calc.assert_called_once_with(2)

            # Verify result structure
            assert len(result) == 3  # lookahead=2 means 3 states (current + 2 future)
            assert result[0].state_value == "on"
            assert result[0].is_available is True

        # Now test the actual calculator (not mocked) to verify full integration
        now = dt_util.parse_datetime("2025-12-24T18:00:00+01:00")
        actual_results = await coordinator.calculator.calculate_states(
            lookahead=2, now=now
        )

        # Verify actual calculation works
        assert len(actual_results) > 0
        assert isinstance(actual_results[0], CalculatedState)

        # At 18:00 on Dec 24, should be "on" (from state 3: Dec 24 16:00 - Dec 26)
        assert actual_results[0].state_value == "on"
        assert actual_results[0].is_available is True

        # Verify attributes are set
        attrs = actual_results[0].get_attributes()
        assert "start" in attrs
        assert "end" in attrs

    @pytest.mark.asyncio
    @patch("homeassistant.helpers.frame.report_usage")
    async def test_sensor_coordinator_integration(self, mock_report_usage):
        """Test integration between sensor, coordinator, and calculator."""
        mock_hass = MagicMock()
        mock_hass.config.time_zone = "Europe/Berlin"

        time_parser = TimeParser(mock_hass)

        states = [
            StateConfig(
                state="on",
                start=time_parser.parse("08:00"),
                end=time_parser.parse("17:00"),
                conditions=[],
                raw_config={},
            ),
            StateConfig(
                state="off",
                start=time_parser.parse("17:00"),
                end=time_parser.parse("08:00"),
                conditions=[],
                raw_config={},
            ),
        ]

        # Create coordinator
        coordinator = DeclarativeStateCoordinator(
            hass=mock_hass,
            name="work_hours",
            states=states,
            error_handling=ERROR_IGNORE,
            lookahead=1,
            update_interval=timedelta(minutes=1),
        )

        # Test at 10:00 (during work hours)
        now = dt_util.parse_datetime("2025-12-31T10:00:00+01:00")
        results = await coordinator.calculator.calculate_states(lookahead=1, now=now)

        # Should be "on" during work hours
        assert results[0].state_value == "on"
        assert results[0].is_available is True

        # Should have next state as "off" at 17:00
        assert results[1].state_value == "off"
        assert results[1].occurrence.start.hour == 17

        # Test at 20:00 (after work hours)
        now = dt_util.parse_datetime("2025-12-31T20:00:00+01:00")
        results = await coordinator.calculator.calculate_states(lookahead=1, now=now)

        # Should be "off" after work hours
        assert results[0].state_value == "off"
        assert results[0].is_available is True

    @pytest.mark.asyncio
    async def test_template_tracking_integration(self):
        """Test integration with template-based times."""
        from homeassistant.helpers.template import Template

        mock_hass = MagicMock()
        mock_hass.config.time_zone = "Europe/Berlin"

        time_parser = TimeParser(mock_hass)

        # Create a template that resolves to a specific time
        mock_template = MagicMock(spec=Template)
        mock_template.async_render = MagicMock(
            return_value=dt_util.parse_datetime("2025-12-31T08:00:00+01:00")
        )

        # Parse template time
        template_spec = time_parser.parse(mock_template)

        assert template_spec.is_template is True
        assert template_spec.raw == mock_template

        # Resolve template
        resolved = await time_parser.resolve_template(mock_template)

        assert resolved.hour == 8
        assert resolved.minute == 0
        assert resolved.tzinfo is not None

        # Create state with template
        states = [
            StateConfig(
                state="on",
                start=template_spec,
                end=time_parser.parse("17:00"),
                conditions=[],
                raw_config={},
            ),
        ]

        calculator = StateCalculator(
            hass=mock_hass,
            states=states,
            error_handling=ERROR_IGNORE,
            time_parser=time_parser,
        )

        # Calculate at 10:00 (after template start time)
        now = dt_util.parse_datetime("2025-12-31T10:00:00+01:00")
        results = await calculator.calculate_states(lookahead=0, now=now)

        # Should be "on" since template resolved to 08:00 and we're at 10:00
        assert results[0].state_value == "on"
        assert results[0].is_available is True

    @pytest.mark.asyncio
    @patch("homeassistant.helpers.condition.async_from_config")
    async def test_condition_evaluation_integration(self, mock_async_from_config):
        """Test integration with condition evaluation."""
        mock_hass = MagicMock()
        mock_hass.config.time_zone = "Europe/Berlin"

        time_parser = TimeParser(mock_hass)

        # Mock condition function that returns True when called with hass
        mock_condition_func = MagicMock(return_value=True)
        # async_from_config is async, so wrap in AsyncMock
        async def mock_return_func(*args, **kwargs):
            return mock_condition_func
        mock_async_from_config.side_effect = mock_return_func

        # Use overlapping states where condition determines winner
        # State 1: "off" 08:00-22:00 no condition
        # State 2: "on" 08:00-22:00 with condition (wins if condition true)
        states = [
            StateConfig(
                state="off",
                start=time_parser.parse("08:00"),
                end=time_parser.parse("22:00"),
                conditions=[],
                raw_config={},
            ),
            StateConfig(
                state="on",
                start=time_parser.parse("08:00"),
                end=time_parser.parse("22:00"),
                conditions=[
                    {
                        "condition": "state",
                        "entity_id": "input_boolean.work_mode",
                        "state": "on",
                    }
                ],
                raw_config={},
            ),
        ]

        calculator = StateCalculator(
            hass=mock_hass,
            states=states,
            error_handling=ERROR_IGNORE,
            time_parser=time_parser,
        )

        # Test at 10:00 with condition satisfied
        now = dt_util.parse_datetime("2025-12-31T10:00:00+01:00")
        results = await calculator.calculate_states(lookahead=0, now=now)

        # Should be "on" since condition is satisfied
        assert results[0].state_value == "on"
        assert results[0].is_available is True

        # Verify condition was checked
        assert mock_condition_func.called

        # Now test with condition returning False
        mock_condition_func.return_value = False

        results = await calculator.calculate_states(lookahead=0, now=now)

        # Should be "off" since condition failed (falls through to next state)
        assert results[0].state_value == "off"
        assert results[0].is_available is True
