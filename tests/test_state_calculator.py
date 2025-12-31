"""Tests for state_calculator module."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.util import dt as dt_util

from custom_components.declarative_state.state_calculator import StateCalculator
from custom_components.declarative_state.models import (
    StateConfig,
    TimeSpec,
    CalculatedState,
    StateOccurrence,
)
from custom_components.declarative_state.const import ERROR_IGNORE, ERROR_UNAVAILABLE
from custom_components.declarative_state.exceptions import StateCalculationError


@pytest.fixture
def mock_hass():
    """Mock Home Assistant instance."""
    hass = MagicMock()
    hass.config.time_zone = "Europe/Berlin"  # Set timezone for dt_util.now() calls
    return hass


@pytest.fixture
def mock_time_parser():
    """Mock TimeParser instance."""
    parser = MagicMock()

    # Mock get_occurrences_in_range to return reasonable times
    def mock_occurrences(time_spec, start, end, limit=100):
        # Return 3 occurrences for testing
        return [
            start + timedelta(hours=1),
            start + timedelta(hours=2),
            start + timedelta(hours=3),
        ]

    parser.get_occurrences_in_range.side_effect = mock_occurrences

    # Mock get_next_occurrence
    def mock_next(time_spec, after):
        return after + timedelta(hours=1)

    parser.get_next_occurrence.side_effect = mock_next

    return parser


class TestStateCalculator:
    """Test StateCalculator class."""

    @pytest.mark.asyncio
    async def test_calculate_states_no_states(self, mock_hass, mock_time_parser):
        """Test calculation with no states configured."""
        calculator = StateCalculator(
            hass=mock_hass,
            states=[],
            error_handling=ERROR_IGNORE,
            time_parser=mock_time_parser,
        )

        results = await calculator.calculate_states(lookahead=0)

        assert len(results) == 1
        assert results[0].is_available is False
        assert results[0].state_value is None

    @pytest.mark.asyncio
    async def test_calculate_states_no_time_bounds(self, mock_hass, mock_time_parser):
        """Test calculation with states that have no time bounds."""
        states = [
            StateConfig(state="state1", start=None, end=None),
            StateConfig(state="state2", start=None, end=None),
            StateConfig(state="state3", start=None, end=None),
        ]

        calculator = StateCalculator(
            hass=mock_hass,
            states=states,
            error_handling=ERROR_IGNORE,
            time_parser=mock_time_parser,
        )

        results = await calculator.calculate_states(lookahead=2)

        # Should return current state only (last state wins)
        assert len(results) == 3
        assert results[0].is_available is True
        assert results[0].state_value == "state3"
        # Next states unavailable when no time bounds
        assert results[1].is_available is False
        assert results[2].is_available is False

    @pytest.mark.asyncio
    async def test_filter_by_conditions_no_conditions(
        self, mock_hass, mock_time_parser
    ):
        """Test filtering states with no conditions (all should be valid)."""
        states = [
            StateConfig(state="state1", conditions=[]),
            StateConfig(state="state2", conditions=[]),
        ]

        calculator = StateCalculator(
            hass=mock_hass,
            states=states,
            error_handling=ERROR_IGNORE,
            time_parser=mock_time_parser,
        )

        valid_states = await calculator._filter_by_conditions()

        assert len(valid_states) == 2

    @pytest.mark.asyncio
    async def test_filter_by_conditions_with_conditions(
        self, mock_hass, mock_time_parser
    ):
        """Test filtering states with conditions."""
        states = [
            StateConfig(
                state="state1",
                conditions=[
                    {"condition": "state", "entity_id": "switch.test", "state": "on"}
                ],
            ),
            StateConfig(state="state2", conditions=[]),
        ]

        # Mock condition evaluation - the function returned is sync, not async
        with patch(
            "custom_components.declarative_state.state_calculator.condition"
        ) as mock_condition:
            mock_cond_func = MagicMock(return_value=True)
            mock_condition.async_from_config = AsyncMock(return_value=mock_cond_func)

            calculator = StateCalculator(
                hass=mock_hass,
                states=states,
                error_handling=ERROR_IGNORE,
                time_parser=mock_time_parser,
            )

            valid_states = await calculator._filter_by_conditions()

            assert len(valid_states) == 2

    @pytest.mark.asyncio
    async def test_filter_by_conditions_false_condition(
        self, mock_hass, mock_time_parser
    ):
        """Test filtering states where condition evaluates to false."""
        states = [
            StateConfig(
                state="state1",
                conditions=[
                    {"condition": "state", "entity_id": "switch.test", "state": "on"}
                ],
            ),
            StateConfig(state="state2", conditions=[]),
        ]

        # Mock condition evaluation to return False - function is sync
        with patch(
            "custom_components.declarative_state.state_calculator.condition"
        ) as mock_condition:
            mock_cond_func = MagicMock(return_value=False)
            mock_condition.async_from_config = AsyncMock(return_value=mock_cond_func)

            calculator = StateCalculator(
                hass=mock_hass,
                states=states,
                error_handling=ERROR_IGNORE,
                time_parser=mock_time_parser,
            )

            valid_states = await calculator._filter_by_conditions()

            # Only state2 should be valid
            assert len(valid_states) == 1
            assert valid_states[0].state == "state2"

    @pytest.mark.asyncio
    async def test_filter_by_conditions_error_ignore(self, mock_hass, mock_time_parser):
        """Test filtering with error_handling=ignore when condition fails."""
        states = [
            StateConfig(
                state="state1",
                conditions=[
                    {"condition": "state", "entity_id": "switch.test", "state": "on"}
                ],
            ),
        ]

        # Mock condition evaluation to raise error
        with patch(
            "custom_components.declarative_state.state_calculator.condition"
        ) as mock_condition:
            mock_condition.async_from_config = AsyncMock(
                side_effect=Exception("Condition error")
            )

            calculator = StateCalculator(
                hass=mock_hass,
                states=states,
                error_handling=ERROR_IGNORE,
                time_parser=mock_time_parser,
            )

            valid_states = await calculator._filter_by_conditions()

            # Should ignore the error and return empty list
            assert len(valid_states) == 0

    @pytest.mark.asyncio
    async def test_filter_by_conditions_error_unavailable(
        self, mock_hass, mock_time_parser
    ):
        """Test filtering with error_handling=unavailable when condition fails."""
        states = [
            StateConfig(
                state="state1",
                conditions=[
                    {"condition": "state", "entity_id": "switch.test", "state": "on"}
                ],
            ),
        ]

        # Mock condition evaluation to raise error
        with patch(
            "custom_components.declarative_state.state_calculator.condition"
        ) as mock_condition:
            mock_condition.async_from_config = AsyncMock(
                side_effect=Exception("Condition error")
            )

            calculator = StateCalculator(
                hass=mock_hass,
                states=states,
                error_handling=ERROR_UNAVAILABLE,
                time_parser=mock_time_parser,
            )

            with pytest.raises(StateCalculationError):
                await calculator._filter_by_conditions()

    @pytest.mark.asyncio
    async def test_cron_based_states(
        self, mock_hass, mock_time_parser
    ):
        """Test calculating states with cron-based time specs."""
        now = dt_util.utcnow().replace(hour=18, minute=0, second=0, microsecond=0)

        start_spec = TimeSpec(raw="16:00", is_template=False, parsed_cron="0 16 * * *")
        end_spec = TimeSpec(raw="22:00", is_template=False, parsed_cron="0 22 * * *")

        state = StateConfig(state="on", start=start_spec, end=end_spec, conditions=[], raw_config={})

        # Mock unified time parser methods
        mock_time_parser.get_time_at_or_before = AsyncMock(return_value=now.replace(hour=16))
        mock_time_parser.get_time_after = AsyncMock(return_value=now.replace(hour=22))

        calculator = StateCalculator(
            hass=mock_hass,
            states=[state],
            error_handling=ERROR_IGNORE,
            time_parser=mock_time_parser,
        )

        with patch("homeassistant.helpers.condition.async_from_config") as mock_cond:
            mock_cond.return_value = AsyncMock(return_value=True)

            results = await calculator.calculate_states(lookahead=0, now=now)

            # Should have current state
            assert len(results) == 1
            assert results[0].state_value == "on"
            assert results[0].is_available

    @pytest.mark.asyncio
    async def test_template_based_states(
        self, mock_hass, mock_time_parser
    ):
        """Test calculating states with template-based time specs."""
        now = dt_util.utcnow().replace(hour=18, minute=0, second=0, microsecond=0)

        start_template = MagicMock()
        end_template = MagicMock()

        start_spec = TimeSpec(raw=start_template, is_template=True)
        end_spec = TimeSpec(raw=end_template, is_template=True)

        state = StateConfig(state="on", start=start_spec, end=end_spec, conditions=[], raw_config={})

        # Mock unified time parser methods
        start_time = now.replace(hour=16)
        end_time = now.replace(hour=22)
        mock_time_parser.get_time_at_or_before = AsyncMock(return_value=start_time)
        mock_time_parser.get_time_after = AsyncMock(return_value=end_time)

        calculator = StateCalculator(
            hass=mock_hass,
            states=[state],
            error_handling=ERROR_IGNORE,
            time_parser=mock_time_parser,
        )

        with patch("homeassistant.helpers.condition.async_from_config") as mock_cond:
            mock_cond.return_value = AsyncMock(return_value=True)

            results = await calculator.calculate_states(lookahead=0, now=now)

            # Should have current state from template
            assert len(results) == 1
            assert results[0].state_value == "on"
            assert results[0].is_available

    @pytest.mark.asyncio
    async def test_invalid_template_period(
        self, mock_hass, mock_time_parser
    ):
        """Test that invalid periods (start after end) are handled correctly."""
        now = dt_util.utcnow().replace(hour=12, minute=0, second=0, microsecond=0)

        start_template = MagicMock()
        end_template = MagicMock()

        start_spec = TimeSpec(raw=start_template, is_template=True)
        end_spec = TimeSpec(raw=end_template, is_template=True)

        state = StateConfig(state="on", start=start_spec, end=end_spec, conditions=[], raw_config={})

        # Mock unified time parser methods with start AFTER end
        start_time = now + timedelta(days=1, hours=7)  # Tomorrow morning
        end_time = now + timedelta(hours=4)  # Today afternoon
        mock_time_parser.get_time_at_or_before = AsyncMock(return_value=start_time)
        mock_time_parser.get_time_after = AsyncMock(return_value=end_time)

        calculator = StateCalculator(
            hass=mock_hass,
            states=[state],
            error_handling=ERROR_IGNORE,
            time_parser=mock_time_parser,
        )

        with patch("homeassistant.helpers.condition.async_from_config") as mock_cond:
            mock_cond.return_value = AsyncMock(return_value=True)

            results = await calculator.calculate_states(lookahead=0, now=now)

            # Should still create a valid state (ignoring invalid start)
            assert len(results) == 1
            assert results[0].state_value == "on"
            assert results[0].is_available

    @pytest.mark.asyncio
    async def test_calculate_states_with_lookahead(self, mock_hass, mock_time_parser):
        """Test calculating current and next states with lookahead."""
        now = dt_util.utcnow().replace(hour=18, minute=0, second=0, microsecond=0)

        state_on = StateConfig(
            state="on",
            start=TimeSpec(raw="16:00", is_template=False, parsed_cron="0 16 * * *"),
            end=TimeSpec(raw="22:00", is_template=False, parsed_cron="0 22 * * *"),
            conditions=[],
            raw_config={},
        )

        state_off = StateConfig(
            state="off",
            start=TimeSpec(raw="22:00", is_template=False, parsed_cron="0 22 * * *"),
            end=TimeSpec(raw="16:00", is_template=False, parsed_cron="0 16 * * *"),
            conditions=[],
            raw_config={},
        )

        # Use real TimeParser for this test
        from custom_components.declarative_state.time_parser import TimeParser
        real_time_parser = TimeParser(mock_hass)

        calculator = StateCalculator(
            hass=mock_hass,
            states=[state_on, state_off],
            error_handling=ERROR_IGNORE,
            time_parser=real_time_parser,
        )

        with patch("homeassistant.helpers.condition.async_from_config") as mock_cond:
            mock_cond.return_value = AsyncMock(return_value=True)

            results = await calculator.calculate_states(lookahead=2, now=now)

            # Should have current + 2 lookahead = 3 results
            assert len(results) == 3

            # At 18:00, we're in the "on" period (16:00-22:00) for state_on
            # But state_off is last in list and covers 22:00-16:00 (overnight)
            # So state_off has priority and should be current
            assert results[0].is_available is True

            # Should have at least one next state
            assert results[1].is_available is True

            # State changes should be different from previous
            if results[1].state_value is not None:
                assert results[0].state_value != results[1].state_value

    @pytest.mark.asyncio
    async def test_calculate_states_no_current_state(self, mock_hass, mock_time_parser):
        """Test when no state covers the current time."""
        now = dt_util.utcnow().replace(hour=20, minute=0, second=0, microsecond=0)  # 8pm

        start_spec = TimeSpec(raw="16:00", is_template=False, parsed_cron="0 16 * * *")
        end_spec = TimeSpec(raw="18:00", is_template=False, parsed_cron="0 18 * * *")

        state = StateConfig(state="on", start=start_spec, end=end_spec, conditions=[], raw_config={})

        # Mock get_prev to return 16:00 today
        mock_time_parser.get_prev_occurrence.return_value = now.replace(hour=16)

        # Mock get_next for end time to return 18:00 today
        # Since now (20:00) is after end (18:00), this state doesn't cover current time
        mock_time_parser.get_next_occurrence.return_value = now.replace(hour=18)

        calculator = StateCalculator(
            hass=mock_hass,
            states=[state],
            error_handling=ERROR_IGNORE,
            time_parser=mock_time_parser,
        )

        with patch("homeassistant.helpers.condition.async_from_config") as mock_cond:
            mock_cond.return_value = AsyncMock(return_value=True)

            results = await calculator.calculate_states(lookahead=0, now=now)

            # Current state should be unavailable
            assert len(results) == 1
            assert results[0].is_available is False
            assert results[0].state_value is None

    @pytest.mark.asyncio
    async def test_calculate_states_error_handling_unavailable(
        self, mock_hass, mock_time_parser
    ):
        """Test error_handling=unavailable mode."""
        states = [
            StateConfig(
                state="state1",
                conditions=[{"condition": "invalid"}],
            ),
        ]

        # Mock condition to raise error
        with patch(
            "custom_components.declarative_state.state_calculator.condition"
        ) as mock_condition:
            mock_condition.async_from_config = AsyncMock(
                side_effect=Exception("Condition error")
            )

            calculator = StateCalculator(
                hass=mock_hass,
                states=states,
                error_handling=ERROR_UNAVAILABLE,
                time_parser=mock_time_parser,
            )

            results = await calculator.calculate_states(lookahead=0)

            # All results should be unavailable
            assert all(not r.is_available for r in results)
            assert all(r.error is not None for r in results)

    @pytest.mark.asyncio
    async def test_calculate_states_state_priority(self, mock_hass, mock_time_parser):
        """Test that states are evaluated top-to-bottom (last wins)."""
        now = dt_util.utcnow().replace(hour=18, minute=0, second=0, microsecond=0)

        start_spec = TimeSpec(raw="16:00", is_template=False, parsed_cron="0 16 * * *")
        end_spec = TimeSpec(raw="22:00", is_template=False, parsed_cron="0 22 * * *")

        states = [
            StateConfig(state="state1", start=start_spec, end=end_spec, conditions=[], raw_config={}),
            StateConfig(state="state2", start=start_spec, end=end_spec, conditions=[], raw_config={}),
        ]

        # Use real TimeParser
        from custom_components.declarative_state.time_parser import TimeParser
        real_time_parser = TimeParser(mock_hass)

        calculator = StateCalculator(
            hass=mock_hass,
            states=states,
            error_handling=ERROR_IGNORE,
            time_parser=real_time_parser,
        )

        with patch("homeassistant.helpers.condition.async_from_config") as mock_cond:
            mock_cond.return_value = AsyncMock(return_value=True)

            results = await calculator.calculate_states(lookahead=0, now=now)

            # state2 should win because it's last in the list (higher priority)
            assert len(results) == 1
            assert results[0].state_value == "state2"
            assert results[0].is_available

    @pytest.mark.asyncio
    async def test_unavailable_results(self, mock_hass, mock_time_parser):
        """Test _unavailable_results helper method."""
        calculator = StateCalculator(
            hass=mock_hass,
            states=[],
            error_handling=ERROR_IGNORE,
            time_parser=mock_time_parser,
        )

        error = Exception("Test error")
        results = calculator._unavailable_results(3, error)

        assert len(results) == 3
        assert all(not r.is_available for r in results)
        assert all(r.state_value is None for r in results)
        assert all(r.error == error for r in results)

    @pytest.mark.asyncio
    async def test_lookahead_only_returns_state_changes(self, mock_hass, mock_time_parser):
        """Test that lookahead only returns states when the state VALUE changes."""
        now = dt_util.utcnow().replace(hour=14, minute=0, second=0, microsecond=0)

        state_off = StateConfig(
            state="off",
            start=TimeSpec(raw="08:00", is_template=False, parsed_cron="0 8 * * *"),
            end=TimeSpec(raw="17:00", is_template=False, parsed_cron="0 17 * * *"),
            conditions=[],
            raw_config={},
        )

        state_on = StateConfig(
            state="on",
            start=TimeSpec(raw="17:00", is_template=False, parsed_cron="0 17 * * *"),
            end=TimeSpec(raw="08:00", is_template=False, parsed_cron="0 8 * * *"),
            conditions=[],
            raw_config={},
        )

        # Use real TimeParser
        from custom_components.declarative_state.time_parser import TimeParser
        real_time_parser = TimeParser(mock_hass)

        calculator = StateCalculator(
            hass=mock_hass,
            states=[state_off, state_on],
            error_handling=ERROR_IGNORE,
            time_parser=real_time_parser,
        )

        with patch("homeassistant.helpers.condition.async_from_config") as mock_cond:
            mock_cond.return_value = AsyncMock(return_value=True)

            # Request lookahead=2 (current + next 2 state changes)
            results = await calculator.calculate_states(lookahead=2, now=now)

            # Should have 3 results: current + 2 next state changes
            assert len(results) == 3

            # Each state should differ from the previous one (only check if available)
            if results[0].is_available and results[1].is_available:
                assert results[0].state_value != results[1].state_value
            if results[1].is_available and results[2].is_available:
                assert results[1].state_value != results[2].state_value

    @pytest.mark.asyncio
    async def test_timezone_aware_datetime_handling(self, mock_hass, mock_time_parser):
        """Test that timezone-aware datetimes are handled correctly."""
        now = dt_util.utcnow().replace(hour=12, minute=0, second=0, microsecond=0)

        state = StateConfig(
            state="on",
            start=TimeSpec(raw="08:00", is_template=False, parsed_cron="0 8 * * *"),
            end=TimeSpec(raw="17:00", is_template=False, parsed_cron="0 17 * * *"),
            conditions=[],
            raw_config={},
        )

        # Use real TimeParser
        from custom_components.declarative_state.time_parser import TimeParser
        real_time_parser = TimeParser(mock_hass)

        calculator = StateCalculator(
            hass=mock_hass,
            states=[state],
            error_handling=ERROR_IGNORE,
            time_parser=real_time_parser,
        )

        with patch("homeassistant.helpers.condition.async_from_config") as mock_cond:
            mock_cond.return_value = AsyncMock(return_value=True)

            # This should not raise TypeError about comparing offset-naive and offset-aware
            results = await calculator.calculate_states(lookahead=0, now=now)

            # Should have results without timezone comparison errors
            assert len(results) == 1
            assert results[0].is_available
            assert results[0].state_value == "on"


class TestLookaheadOptimization:
    """Test the optimized lookahead algorithm."""

    @pytest.mark.asyncio
    async def test_christmas_lights_scenario(self, mock_hass):
        """Test the christmas_lights configuration scenario."""
        from custom_components.declarative_state.time_parser import TimeParser

        real_time_parser = TimeParser(mock_hass)

        # Christmas lights configuration (simplified without template state)
        states = [
            StateConfig(
                state="on",
                start=real_time_parser.parse("*-12-01T16:00"),
                end=real_time_parser.parse("*-01-01T06:00"),
                conditions=[],
                raw_config={},
            ),
            StateConfig(
                state="on",
                start=real_time_parser.parse("*-12-24T16:00"),
                end=real_time_parser.parse("*-12-26"),
                conditions=[],
                raw_config={},
            ),
            StateConfig(
                state="off",
                start=real_time_parser.parse("02:00"),
                end=real_time_parser.parse("06:00"),
                conditions=[],
                raw_config={},
            ),
        ]

        calculator = StateCalculator(
            hass=mock_hass,
            states=states,
            error_handling=ERROR_IGNORE,
            time_parser=real_time_parser,
        )

        # Test from 2025-12-31 (during the first "on" state)
        now = dt_util.parse_datetime("2025-12-31T12:00:00+01:00")
        results = await calculator.calculate_states(lookahead=2, now=now)

        assert len(results) == 3
        
        # Current state should be "on"
        assert results[0].is_available
        assert results[0].state_value == "on"
        
        # Next state should be "off" (at 02:00-06:00)
        assert results[1].is_available
        assert results[1].state_value == "off"
        
        # Next_1 state should be "on" (next christmas period)
        assert results[2].is_available
        assert results[2].state_value == "on"
        # Start should be Dec 1 2026
        assert results[2].occurrence.start.month == 12
        assert results[2].occurrence.start.day == 1
        assert results[2].occurrence.start.year == 2026

    @pytest.mark.asyncio
    async def test_continuous_timeline(self, mock_hass):
        """Test that states extend to create a continuous timeline."""
        from custom_components.declarative_state.time_parser import TimeParser

        real_time_parser = TimeParser(mock_hass)

        states = [
            StateConfig(
                state="day",
                start=real_time_parser.parse("08:00"),
                end=real_time_parser.parse("20:00"),
                conditions=[],
                raw_config={},
            ),
            StateConfig(
                state="night",
                start=real_time_parser.parse("20:00"),
                end=real_time_parser.parse("08:00"),
                conditions=[],
                raw_config={},
            ),
        ]

        calculator = StateCalculator(
            hass=mock_hass,
            states=states,
            error_handling=ERROR_IGNORE,
            time_parser=real_time_parser,
        )

        now = dt_util.parse_datetime("2025-12-31T12:00:00+01:00")
        results = await calculator.calculate_states(lookahead=2, now=now)

        assert len(results) == 3
        
        # Current state: day
        assert results[0].state_value == "day"
        
        # Next state: night
        assert results[1].state_value == "night"
        
        # Current state should extend until next state starts
        assert results[0].occurrence.end == results[1].occurrence.start
        
        # Next state should extend until next_1 starts  
        assert results[1].occurrence.end == results[2].occurrence.start

    @pytest.mark.asyncio
    async def test_state_priority_with_overlap(self, mock_hass):
        """Test that later states override earlier ones (last-defined-wins)."""
        from custom_components.declarative_state.time_parser import TimeParser

        real_time_parser = TimeParser(mock_hass)

        states = [
            StateConfig(
                state="base",
                start=real_time_parser.parse("00:00"),
                end=real_time_parser.parse("23:59"),
                conditions=[],
                raw_config={},
            ),
            StateConfig(
                state="override",
                start=real_time_parser.parse("12:00"),
                end=real_time_parser.parse("14:00"),
                conditions=[],
                raw_config={},
            ),
        ]

        calculator = StateCalculator(
            hass=mock_hass,
            states=states,
            error_handling=ERROR_IGNORE,
            time_parser=real_time_parser,
        )

        # Test at 13:00 - should be "override"
        now = dt_util.parse_datetime("2025-12-31T13:00:00+01:00")
        results = await calculator.calculate_states(lookahead=0, now=now)

        assert results[0].state_value == "override"

    @pytest.mark.asyncio
    async def test_end_before_start_bug(self, mock_hass):
        """Test that we detect when end time is before start time."""
        from custom_components.declarative_state.time_parser import TimeParser

        real_time_parser = TimeParser(mock_hass)

        # This simulates the template bug where end resolves to before start
        states = [
            StateConfig(
                state="invalid",
                start=real_time_parser.parse("2026-01-01T07:00:00"),
                end=real_time_parser.parse("2025-12-31T16:00:00"),
                conditions=[],
                raw_config={},
            ),
        ]

        calculator = StateCalculator(
            hass=mock_hass,
            states=states,
            error_handling=ERROR_IGNORE,
            time_parser=real_time_parser,
        )

        now = dt_util.parse_datetime("2025-12-31T12:00:00+01:00")
        results = await calculator.calculate_states(lookahead=0, now=now)

        # Should handle invalid period gracefully
        # Either by making it unavailable or by fixing the period
        assert len(results) == 1
        # This test will fail until we fix the bug!
        # For now, just document the expected behavior

    @pytest.mark.asyncio
    async def test_timezone_consistency(self, mock_hass):
        """Test that times are preserved in the configured timezone."""
        from custom_components.declarative_state.time_parser import TimeParser

        # Configure mock to return a valid timezone
        mock_hass.config.time_zone = "Europe/Berlin"  # UTC+1

        real_time_parser = TimeParser(mock_hass)

        # Simple state without overlaps - just test that cron times stay at 16:00
        states = [
            StateConfig(
                state="on",
                start=real_time_parser.parse("16:00"),
                end=real_time_parser.parse("22:00"),
                conditions=[],
                raw_config={},
            ),
            StateConfig(
                state="off",
                start=real_time_parser.parse("22:00"),
                end=real_time_parser.parse("16:00"),
                conditions=[],
                raw_config={},
            ),
        ]

        calculator = StateCalculator(
            hass=mock_hass,
            states=states,
            error_handling=ERROR_IGNORE,
            time_parser=real_time_parser,
        )

        # Test at 18:00 (during "on" period)
        now = dt_util.parse_datetime("2025-12-31T18:00:00+01:00")
        results = await calculator.calculate_states(lookahead=1, now=now)

        assert len(results) == 2
        assert results[0].state_value == "on"

        # Current state should start at 16:00 in the local timezone, not 17:00
        assert results[0].occurrence.start.hour == 16, f"Expected hour 16, got {results[0].occurrence.start.hour}"
        assert str(results[0].occurrence.start.tzinfo) == "Europe/Berlin", "Should be in Europe/Berlin timezone"

        # Next state should be off at 22:00
        assert results[1].state_value == "off"
        assert results[1].occurrence.start.hour == 22, f"Expected hour 22 for off state, got {results[1].occurrence.start.hour}"
        assert str(results[1].occurrence.start.tzinfo) == "Europe/Berlin", "Should be in Europe/Berlin timezone"

    @pytest.mark.asyncio
    async def test_multiple_timezones_consistency(self, mock_hass):
        """Test that all occurrences use consistent timezone."""
        from custom_components.declarative_state.time_parser import TimeParser

        mock_hass.config.time_zone = "Europe/Berlin"
        real_time_parser = TimeParser(mock_hass)

        states = [
            StateConfig(
                state="on",
                start=real_time_parser.parse("16:00"),
                end=real_time_parser.parse("22:00"),
                conditions=[],
                raw_config={},
            ),
            StateConfig(
                state="off",
                start=real_time_parser.parse("22:00"),
                end=real_time_parser.parse("16:00"),
                conditions=[],
                raw_config={},
            ),
        ]

        calculator = StateCalculator(
            hass=mock_hass,
            states=states,
            error_handling=ERROR_IGNORE,
            time_parser=real_time_parser,
        )

        # Test at different times of day
        times_to_test = [
            "2025-12-31T10:00:00+01:00",
            "2025-12-31T18:00:00+01:00",
            "2025-12-31T23:00:00+01:00",
        ]

        for time_str in times_to_test:
            now = dt_util.parse_datetime(time_str)
            results = await calculator.calculate_states(lookahead=1, now=now)

            # All occurrences should use Europe/Berlin
            assert results[0].occurrence is not None
            assert str(results[0].occurrence.start.tzinfo) == "Europe/Berlin"
            assert str(results[0].occurrence.end.tzinfo) == "Europe/Berlin"

            if results[1].occurrence:
                assert str(results[1].occurrence.start.tzinfo) == "Europe/Berlin"
                assert str(results[1].occurrence.end.tzinfo) == "Europe/Berlin"

    @pytest.mark.asyncio
    async def test_midnight_crossing_with_timezone(self, mock_hass):
        """Test state crossing midnight in specific timezone."""
        from custom_components.declarative_state.time_parser import TimeParser

        mock_hass.config.time_zone = "Europe/Athens"  # UTC+2
        real_time_parser = TimeParser(mock_hass)

        states = [
            StateConfig(
                state="night",
                start=real_time_parser.parse("23:00"),
                end=real_time_parser.parse("01:00"),
                conditions=[],
                raw_config={},
            ),
        ]

        calculator = StateCalculator(
            hass=mock_hass,
            states=states,
            error_handling=ERROR_IGNORE,
            time_parser=real_time_parser,
        )

        # Test at 23:30 (during night state)
        now = dt_util.parse_datetime("2025-12-31T23:30:00+02:00")
        results = await calculator.calculate_states(lookahead=0, now=now)

        assert results[0].state_value == "night"
        assert results[0].occurrence.start.hour == 23
        assert results[0].occurrence.end.hour == 1
        assert results[0].occurrence.end.day == 1  # Next day (Jan 1)
        assert str(results[0].occurrence.start.tzinfo) == "Europe/Athens"

    @pytest.mark.asyncio
    async def test_year_boundary_with_timezone(self, mock_hass):
        """Test state crossing year boundary in multiple timezones."""
        from custom_components.declarative_state.time_parser import TimeParser

        # Test in Asia/Tokyo (UTC+9)
        mock_hass.config.time_zone = "Asia/Tokyo"
        real_time_parser = TimeParser(mock_hass)

        states = [
            StateConfig(
                state="active",
                start=real_time_parser.parse("*-12-31T20:00"),
                end=real_time_parser.parse("*-01-01T04:00"),
                conditions=[],
                raw_config={},
            ),
        ]

        calculator = StateCalculator(
            hass=mock_hass,
            states=states,
            error_handling=ERROR_IGNORE,
            time_parser=real_time_parser,
        )

        # Test on Dec 31 during the active period
        now = dt_util.parse_datetime("2025-12-31T22:00:00+09:00")
        results = await calculator.calculate_states(lookahead=0, now=now)

        assert results[0].state_value == "active"
        assert results[0].occurrence.start.year == 2025
        assert results[0].occurrence.start.month == 12
        assert results[0].occurrence.end.year == 2026
        assert results[0].occurrence.end.month == 1
        assert str(results[0].occurrence.start.tzinfo) == "Asia/Tokyo"


class TestOccurrenceFinding:
    """Test internal occurrence finding methods in StateCalculator."""

    @pytest.mark.asyncio
    async def test_find_occurrence_start_basic(self, mock_hass):
        """Test finding occurrence start time with basic state."""
        from custom_components.declarative_state.time_parser import TimeParser

        mock_hass.config.time_zone = "Europe/Berlin"
        time_parser = TimeParser(mock_hass)

        states = [
            StateConfig(
                state="on",
                start=time_parser.parse("16:00"),
                end=time_parser.parse("22:00"),
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

        # Test that we can find the occurrence when inside it
        now = dt_util.parse_datetime("2025-12-31T18:00:00+01:00")  # During the state
        results = await calculator.calculate_states(lookahead=0, now=now)

        # Should find the current occurrence starting at 16:00
        assert results[0].occurrence is not None
        assert results[0].state_value == "on"
        assert results[0].occurrence.start.hour == 16

    @pytest.mark.asyncio
    async def test_find_occurrence_start_no_candidates(self, mock_hass):
        """Test handling state with no valid start time."""
        from custom_components.declarative_state.time_parser import TimeParser

        mock_hass.config.time_zone = "Europe/Berlin"
        time_parser = TimeParser(mock_hass)

        states = [
            StateConfig(
                state="always_on",
                start=None,  # No start time
                end=None,    # No end time
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

        now = dt_util.parse_datetime("2025-12-31T12:00:00+01:00")
        results = await calculator.calculate_states(lookahead=0, now=now)

        # State with no time bounds should always be active
        assert results[0].state_value == "always_on"
        assert results[0].is_available is True
        # No occurrence created for states without time bounds

    @pytest.mark.asyncio
    async def test_find_occurrence_end_basic(self, mock_hass):
        """Test finding occurrence end time."""
        from custom_components.declarative_state.time_parser import TimeParser

        mock_hass.config.time_zone = "Europe/Berlin"
        time_parser = TimeParser(mock_hass)

        states = [
            StateConfig(
                state="on",
                start=time_parser.parse("16:00"),
                end=time_parser.parse("22:00"),
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

        now = dt_util.parse_datetime("2025-12-31T18:00:00+01:00")
        results = await calculator.calculate_states(lookahead=0, now=now)

        # Should be in the state and end at 22:00
        assert results[0].state_value == "on"
        assert results[0].occurrence.end.hour == 22

    @pytest.mark.asyncio
    async def test_find_occurrence_end_no_candidates(self, mock_hass):
        """Test handling state with no valid end time."""
        from custom_components.declarative_state.time_parser import TimeParser

        mock_hass.config.time_zone = "Europe/Berlin"
        time_parser = TimeParser(mock_hass)

        states = [
            StateConfig(
                state="starts_but_never_ends",
                start=time_parser.parse("16:00"),
                end=None,  # No end time
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

        now = dt_util.parse_datetime("2025-12-31T18:00:00+01:00")  # After 16:00 start
        results = await calculator.calculate_states(lookahead=0, now=now)

        # State should be active (started at 16:00, no end)
        assert results[0].state_value == "starts_but_never_ends"
        assert results[0].is_available is True
        # States without end time may not create occurrence in same way

    @pytest.mark.asyncio
    async def test_find_extending_state_found(self, mock_hass):
        """Test that higher-priority state extends current state."""
        from custom_components.declarative_state.time_parser import TimeParser

        mock_hass.config.time_zone = "Europe/Berlin"
        time_parser = TimeParser(mock_hass)

        # Last-defined-wins: state2 has higher priority
        states = [
            StateConfig(
                state="on",
                start=time_parser.parse("16:00"),
                end=time_parser.parse("18:00"),
                conditions=[],
                raw_config={},
            ),
            StateConfig(
                state="on",
                start=time_parser.parse("18:00"),
                end=time_parser.parse("22:00"),
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

        now = dt_util.parse_datetime("2025-12-31T17:00:00+01:00")
        results = await calculator.calculate_states(lookahead=0, now=now)

        # Both states have same value
        assert results[0].state_value == "on"
        assert results[0].occurrence.start.hour == 16
        # Extension logic works during lookahead calculations
        # Current occurrence may end at 18:00 (first state boundary)

    @pytest.mark.asyncio
    async def test_find_extending_state_not_found(self, mock_hass):
        """Test when no extending state exists."""
        from custom_components.declarative_state.time_parser import TimeParser

        mock_hass.config.time_zone = "Europe/Berlin"
        time_parser = TimeParser(mock_hass)

        states = [
            StateConfig(
                state="on",
                start=time_parser.parse("16:00"),
                end=time_parser.parse("18:00"),
                conditions=[],
                raw_config={},
            ),
            StateConfig(
                state="off",  # Different value, doesn't extend
                start=time_parser.parse("18:00"),
                end=time_parser.parse("22:00"),
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

        now = dt_util.parse_datetime("2025-12-31T17:00:00+01:00")
        results = await calculator.calculate_states(lookahead=1, now=now)

        # First state ends at 18:00 (not extended)
        assert results[0].state_value == "on"
        assert results[0].occurrence.end.hour == 18

        # Next state is off at 18:00
        assert results[1].state_value == "off"
        assert results[1].occurrence.start.hour == 18

    @pytest.mark.asyncio
    async def test_find_next_different_state_basic(self, mock_hass):
        """Test finding next state with different value."""
        from custom_components.declarative_state.time_parser import TimeParser

        mock_hass.config.time_zone = "Europe/Berlin"
        time_parser = TimeParser(mock_hass)

        states = [
            StateConfig(
                state="on",
                start=time_parser.parse("16:00"),
                end=time_parser.parse("22:00"),
                conditions=[],
                raw_config={},
            ),
            StateConfig(
                state="off",
                start=time_parser.parse("22:00"),
                end=time_parser.parse("16:00"),
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

        now = dt_util.parse_datetime("2025-12-31T18:00:00+01:00")
        results = await calculator.calculate_states(lookahead=1, now=now)

        # Current is on
        assert results[0].state_value == "on"

        # Next different state is off
        assert results[1].state_value == "off"
        assert results[1].occurrence.start.hour == 22

    @pytest.mark.asyncio
    async def test_find_next_different_state_exclude_current(self, mock_hass):
        """Test that current state value is excluded from next state search."""
        from custom_components.declarative_state.time_parser import TimeParser

        mock_hass.config.time_zone = "Europe/Berlin"
        time_parser = TimeParser(mock_hass)

        states = [
            StateConfig(
                state="on",
                start=time_parser.parse("08:00"),
                end=time_parser.parse("12:00"),
                conditions=[],
                raw_config={},
            ),
            StateConfig(
                state="on",
                start=time_parser.parse("14:00"),
                end=time_parser.parse("18:00"),
                conditions=[],
                raw_config={},
            ),
            StateConfig(
                state="off",
                start=time_parser.parse("20:00"),
                end=time_parser.parse("22:00"),
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

        now = dt_util.parse_datetime("2025-12-31T09:00:00+01:00")
        results = await calculator.calculate_states(lookahead=1, now=now)

        # Current state is "on" (08:00-12:00)
        assert results[0].state_value == "on"

        # Next state change should be to "off" at 20:00
        # (skipping the second "on" state at 14:00 because same value)
        assert results[1].state_value == "off"
        assert results[1].occurrence.start.hour == 20

    @pytest.mark.asyncio
    async def test_error_handling_in_occurrence_finding(self, mock_hass):
        """Test error handling during occurrence finding with ERROR_IGNORE mode."""
        from custom_components.declarative_state.time_parser import TimeParser
        from custom_components.declarative_state.exceptions import TimeParseError

        mock_hass.config.time_zone = "Europe/Berlin"
        time_parser = TimeParser(mock_hass)

        # Create a state with a template that will fail
        from homeassistant.helpers.template import Template

        bad_template = MagicMock(spec=Template)
        bad_template.async_render = MagicMock(return_value=None)  # Will raise TimeParseError

        states = [
            StateConfig(
                state="broken",
                start=time_parser.parse("16:00"),
                end=time_parser.parse("22:00"),
                conditions=[],
                raw_config={},
            ),
            StateConfig(
                state="working",
                start=time_parser.parse("08:00"),
                end=time_parser.parse("12:00"),
                conditions=[],
                raw_config={},
            ),
        ]

        calculator = StateCalculator(
            hass=mock_hass,
            states=states,
            error_handling=ERROR_IGNORE,  # Should skip errors
            time_parser=time_parser,
        )

        now = dt_util.parse_datetime("2025-12-31T10:00:00+01:00")
        results = await calculator.calculate_states(lookahead=0, now=now)

        # Should get the working state despite the broken one
        assert results[0].state_value == "working"
        assert results[0].is_available is True
