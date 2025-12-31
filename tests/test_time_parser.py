"""Tests for time_parser module."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock
from zoneinfo import ZoneInfo

from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util

from custom_components.declarative_state.time_parser import TimeParser
from custom_components.declarative_state.exceptions import TimeParseError


class TestTimeParser:
    """Test TimeParser class."""

    def test_parse_time_only(self, hass):
        """Test parsing HH:MM format."""
        parser = TimeParser(hass)
        time_spec = parser.parse("16:30")

        assert time_spec.is_template is False
        assert time_spec.parsed_cron == "30 16 * * *"
        assert time_spec.raw == "16:30"

    def test_parse_hour_only(self, hass):
        """Test parsing HH format."""
        parser = TimeParser(hass)
        time_spec = parser.parse("16")

        assert time_spec.is_template is False
        assert time_spec.parsed_cron == "0 16 * * *"
        assert time_spec.raw == "16"

    def test_parse_iso8601_with_wildcards(self, hass):
        """Test parsing ISO8601 with wildcards."""
        parser = TimeParser(hass)
        time_spec = parser.parse("*-12-24T16:00")

        assert time_spec.is_template is False
        assert time_spec.parsed_cron == "0 16 24 12 *"
        assert time_spec.raw == "*-12-24T16:00"

    def test_parse_iso8601_all_wildcards(self, hass):
        """Test parsing ISO8601 with all wildcards."""
        parser = TimeParser(hass)
        time_spec = parser.parse("*-*-*T08:30")

        assert time_spec.is_template is False
        assert time_spec.parsed_cron == "30 8 * * *"

    def test_parse_invalid_format(self, hass):
        """Test parsing invalid format raises error."""
        parser = TimeParser(hass)

        with pytest.raises(TimeParseError):
            parser.parse("invalid")

    def test_get_next_occurrence(self, hass):
        """Test getting next occurrence from cron."""
        parser = TimeParser(hass)
        time_spec = parser.parse("16:00")

        now = datetime(2025, 1, 1, 12, 0)
        next_time = parser.get_next_occurrence(time_spec, now)

        assert next_time.hour == 16
        assert next_time.minute == 0
        assert next_time.date() == now.date()


class TestAsyncMethods:
    """Test async methods in TimeParser (previously untested)."""

    @pytest.mark.asyncio
    async def test_resolve_template_datetime_return(self):
        """Test resolve_template when template returns datetime object."""
        mock_hass = MagicMock()
        mock_hass.config.time_zone = "Europe/Berlin"
        parser = TimeParser(mock_hass)

        # Test with timezone-aware datetime
        expected_time = dt_util.parse_datetime("2025-12-25T16:00:00+01:00")
        mock_template = MagicMock(spec=Template)
        mock_template.async_render = MagicMock(return_value=expected_time)

        result = await parser.resolve_template(mock_template)

        assert result == expected_time
        assert result.tzinfo is not None

        # Test with naive datetime (should be converted to local)
        naive_time = datetime(2025, 12, 25, 16, 0, 0)
        mock_template.async_render = MagicMock(return_value=naive_time)

        result = await parser.resolve_template(mock_template)

        assert result.hour == 16
        assert result.tzinfo is not None  # Should be converted

    @pytest.mark.asyncio
    async def test_resolve_template_string_return(self):
        """Test resolve_template when template returns ISO8601 string."""
        mock_hass = MagicMock()
        mock_hass.config.time_zone = "Europe/Berlin"
        parser = TimeParser(mock_hass)

        mock_template = MagicMock(spec=Template)
        mock_template.async_render = MagicMock(return_value="2025-12-25T16:00:00+01:00")

        result = await parser.resolve_template(mock_template)

        assert result.year == 2025
        assert result.month == 12
        assert result.day == 25
        assert result.hour == 16
        assert result.tzinfo is not None

    @pytest.mark.asyncio
    async def test_resolve_template_none_return(self):
        """Test resolve_template when template returns None (raises error)."""
        mock_hass = MagicMock()
        parser = TimeParser(mock_hass)

        mock_template = MagicMock(spec=Template)
        mock_template.async_render = MagicMock(return_value=None)

        with pytest.raises(TimeParseError) as exc_info:
            await parser.resolve_template(mock_template)

        assert "Template returned None" in str(exc_info.value)
        assert "entity may not exist" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_resolve_template_invalid_type(self):
        """Test resolve_template when template returns unexpected type."""
        mock_hass = MagicMock()
        parser = TimeParser(mock_hass)

        mock_template = MagicMock(spec=Template)

        # Test with integer
        mock_template.async_render = MagicMock(return_value=12345)

        with pytest.raises(TimeParseError) as exc_info:
            await parser.resolve_template(mock_template)

        assert "unexpected type" in str(exc_info.value)

        # Test with list
        mock_template.async_render = MagicMock(return_value=["invalid"])

        with pytest.raises(TimeParseError):
            await parser.resolve_template(mock_template)

    @pytest.mark.asyncio
    async def test_get_time_at_or_before_template(self):
        """Test get_time_at_or_before with template time spec."""
        mock_hass = MagicMock()
        mock_hass.config.time_zone = "Europe/Berlin"
        parser = TimeParser(mock_hass)

        search_time = dt_util.parse_datetime("2025-12-25T18:00:00+01:00")

        # Case 1: Template resolves to time BEFORE search time (should return it)
        before_time = dt_util.parse_datetime("2025-12-25T16:00:00+01:00")
        mock_template = MagicMock(spec=Template)
        mock_template.async_render = MagicMock(return_value=before_time)

        time_spec = MagicMock()
        time_spec.is_template = True
        time_spec.raw = mock_template

        result = await parser.get_time_at_or_before(time_spec, search_time)

        assert result == before_time

        # Case 2: Template resolves to time AT search time (should return it)
        at_time = search_time
        mock_template.async_render = MagicMock(return_value=at_time)

        result = await parser.get_time_at_or_before(time_spec, search_time)

        assert result == at_time

        # Case 3: Template resolves to time AFTER search time (should return None)
        after_time = dt_util.parse_datetime("2025-12-25T20:00:00+01:00")
        mock_template.async_render = MagicMock(return_value=after_time)

        result = await parser.get_time_at_or_before(time_spec, search_time)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_time_at_or_before_cron(self):
        """Test get_time_at_or_before with cron time spec."""
        mock_hass = MagicMock()
        mock_hass.config.time_zone = "Europe/Berlin"
        parser = TimeParser(mock_hass)

        time_spec = parser.parse("16:00")  # Daily at 16:00

        # Search at 18:00, should find 16:00 today
        search_time = dt_util.parse_datetime("2025-12-25T18:00:00+01:00")
        result = await parser.get_time_at_or_before(time_spec, search_time)

        assert result is not None
        assert result.hour == 16
        assert result.minute == 0
        assert result.date() == search_time.date()

        # Search at 14:00, should find 16:00 yesterday
        search_time = dt_util.parse_datetime("2025-12-25T14:00:00+01:00")
        result = await parser.get_time_at_or_before(time_spec, search_time)

        assert result is not None
        assert result.hour == 16
        assert result.minute == 0
        assert result.date() == (search_time - timedelta(days=1)).date()

    @pytest.mark.asyncio
    async def test_get_time_after_template(self):
        """Test get_time_after with template time spec."""
        mock_hass = MagicMock()
        mock_hass.config.time_zone = "Europe/Berlin"
        parser = TimeParser(mock_hass)

        search_time = dt_util.parse_datetime("2025-12-25T18:00:00+01:00")

        # Case 1: Template resolves to time AFTER search time (should return it)
        after_time = dt_util.parse_datetime("2025-12-25T20:00:00+01:00")
        mock_template = MagicMock(spec=Template)
        mock_template.async_render = MagicMock(return_value=after_time)

        time_spec = MagicMock()
        time_spec.is_template = True
        time_spec.raw = mock_template

        result = await parser.get_time_after(time_spec, search_time)

        assert result == after_time

        # Case 2: Template resolves to time AT search time (needs day adjustment)
        at_time = search_time
        mock_template.async_render = MagicMock(return_value=at_time)

        result = await parser.get_time_after(time_spec, search_time)

        # Should add 1 day
        assert result > search_time
        assert result.date() == (search_time + timedelta(days=1)).date()

        # Case 3: Template resolves to time BEFORE search time (needs day adjustment)
        before_time = dt_util.parse_datetime("2025-12-25T16:00:00+01:00")
        mock_template.async_render = MagicMock(return_value=before_time)

        result = await parser.get_time_after(time_spec, search_time)

        # Should add days until it's after search_time
        assert result > search_time

    @pytest.mark.asyncio
    async def test_get_time_after_cron(self):
        """Test get_time_after with cron time spec."""
        mock_hass = MagicMock()
        mock_hass.config.time_zone = "Europe/Berlin"
        parser = TimeParser(mock_hass)

        time_spec = parser.parse("16:00")  # Daily at 16:00

        # Search at 14:00, should find 16:00 today
        search_time = dt_util.parse_datetime("2025-12-25T14:00:00+01:00")
        result = await parser.get_time_after(time_spec, search_time)

        assert result is not None
        assert result.hour == 16
        assert result.minute == 0
        assert result.date() == search_time.date()
        assert str(result.tzinfo) == "Europe/Berlin"

        # Search at 18:00, should find 16:00 tomorrow
        search_time = dt_util.parse_datetime("2025-12-25T18:00:00+01:00")
        result = await parser.get_time_after(time_spec, search_time)

        assert result is not None
        assert result.hour == 16
        assert result.minute == 0
        assert result.date() == (search_time + timedelta(days=1)).date()
        assert str(result.tzinfo) == "Europe/Berlin"


class TestTimezoneHandling:
    """Test timezone handling in TimeParser (DST transitions, multiple timezones)."""

    def test_next_occurrence_preserves_timezone(self):
        """Test that get_next_occurrence preserves HA's configured timezone."""
        # Test Europe/Berlin
        mock_hass = MagicMock()
        mock_hass.config.time_zone = "Europe/Berlin"
        parser = TimeParser(mock_hass)

        time_spec = parser.parse("16:00")
        after = dt_util.parse_datetime("2025-12-25T12:00:00+01:00")
        result = parser.get_next_occurrence(time_spec, after)

        assert str(result.tzinfo) == "Europe/Berlin"
        assert result.hour == 16

        # Test America/New_York
        mock_hass.config.time_zone = "America/New_York"
        parser = TimeParser(mock_hass)

        time_spec = parser.parse("16:00")
        after = dt_util.parse_datetime("2025-12-25T12:00:00-05:00")
        result = parser.get_next_occurrence(time_spec, after)

        assert str(result.tzinfo) == "America/New_York"
        assert result.hour == 16

        # Test Asia/Tokyo
        mock_hass.config.time_zone = "Asia/Tokyo"
        parser = TimeParser(mock_hass)

        time_spec = parser.parse("16:00")
        after = dt_util.parse_datetime("2025-12-25T12:00:00+09:00")
        result = parser.get_next_occurrence(time_spec, after)

        assert str(result.tzinfo) == "Asia/Tokyo"
        assert result.hour == 16

    def test_prev_occurrence_preserves_timezone(self):
        """Test that get_prev_occurrence preserves HA's configured timezone."""
        # Test consistency with next_occurrence
        mock_hass = MagicMock()
        mock_hass.config.time_zone = "Europe/Berlin"
        parser = TimeParser(mock_hass)

        time_spec = parser.parse("16:00")
        before = dt_util.parse_datetime("2025-12-25T18:00:00+01:00")
        result = parser.get_prev_occurrence(time_spec, before)

        assert str(result.tzinfo) == "Europe/Berlin"
        assert result.hour == 16
        assert result.date() == before.date()

        # Test America/New_York
        mock_hass.config.time_zone = "America/New_York"
        parser = TimeParser(mock_hass)

        time_spec = parser.parse("14:00")
        before = dt_util.parse_datetime("2025-12-25T18:00:00-05:00")
        result = parser.get_prev_occurrence(time_spec, before)

        assert str(result.tzinfo) == "America/New_York"
        assert result.hour == 14

    def test_dst_spring_forward_us_eastern(self):
        """Test DST spring forward transition in US Eastern timezone."""
        mock_hass = MagicMock()
        mock_hass.config.time_zone = "America/New_York"
        parser = TimeParser(mock_hass)

        # DST Spring Forward: 2025-03-09 02:00 → 03:00 (02:00-03:00 doesn't exist)
        time_spec = parser.parse("02:30")  # Time that doesn't exist during spring forward

        # Get next occurrence just before spring forward
        before_transition = dt_util.parse_datetime("2025-03-09T01:59:00-05:00")
        result = parser.get_next_occurrence(time_spec, before_transition)

        # croniter should skip the non-existent time and return next valid occurrence
        # The next 02:30 after spring forward would be March 10
        assert result.date() > before_transition.date()
        assert str(result.tzinfo) == "America/New_York"

        # Verify time is still in a valid timezone-aware state
        assert result.tzinfo is not None

    def test_dst_fall_back_us_eastern(self):
        """Test DST fall back transition in US Eastern timezone."""
        mock_hass = MagicMock()
        mock_hass.config.time_zone = "America/New_York"
        parser = TimeParser(mock_hass)

        # DST Fall Back: 2025-11-02 02:00 repeats (01:00-02:00 happens twice)
        time_spec = parser.parse("01:30")  # Ambiguous time during fall back

        # Get occurrence during fall back period
        during_transition = dt_util.parse_datetime("2025-11-02T00:30:00-04:00")
        result = parser.get_next_occurrence(time_spec, during_transition)

        # Should return a valid time with timezone info
        assert result.tzinfo is not None
        assert str(result.tzinfo) == "America/New_York"
        assert result.hour == 1
        assert result.minute == 30

        # Verify consistent behavior - get the occurrence
        assert result.date() == during_transition.date()

    def test_dst_spring_forward_europe_london(self):
        """Test DST spring forward in Europe/London (different rules than US)."""
        mock_hass = MagicMock()
        mock_hass.config.time_zone = "Europe/London"
        parser = TimeParser(mock_hass)

        # Europe DST: Last Sunday of March, 01:00 → 02:00
        # 2025-03-30 01:00 GMT → 02:00 BST (01:00-02:00 doesn't exist)
        time_spec = parser.parse("01:30")

        before_transition = dt_util.parse_datetime("2025-03-30T00:30:00+00:00")
        result = parser.get_next_occurrence(time_spec, before_transition)

        # Should skip non-existent time
        assert result.tzinfo is not None
        assert str(result.tzinfo) == "Europe/London"
        # croniter returns 02:30 on same day (time jumps forward to 02:00 BST)
        assert result.date() == before_transition.date()
        # Should be at or after 02:00 (after the spring forward)
        assert result.hour >= 2

    @pytest.mark.asyncio
    async def test_template_resolution_with_timezone(self):
        """Test template resolution handles various timezone scenarios."""
        mock_hass = MagicMock()
        mock_hass.config.time_zone = "Europe/Berlin"
        parser = TimeParser(mock_hass)

        # Case 1: Template returns datetime with specific timezone
        tokyo_time = datetime(2025, 12, 25, 16, 0, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        mock_template = MagicMock(spec=Template)
        mock_template.async_render = MagicMock(return_value=tokyo_time)

        result = await parser.resolve_template(mock_template)

        assert result == tokyo_time
        assert result.tzinfo is not None

        # Case 2: Template returns naive datetime (should convert to local)
        naive_time = datetime(2025, 12, 25, 16, 0, 0)
        mock_template.async_render = MagicMock(return_value=naive_time)

        result = await parser.resolve_template(mock_template)

        assert result.hour == 16
        assert result.tzinfo is not None  # Should be converted to local

        # Case 3: Template returns UTC datetime (should be preserved)
        utc_time = datetime(2025, 12, 25, 16, 0, 0, tzinfo=ZoneInfo("UTC"))
        mock_template.async_render = MagicMock(return_value=utc_time)

        result = await parser.resolve_template(mock_template)

        assert result == utc_time
        assert str(result.tzinfo) == "UTC"

        # Case 4: Template returns string with timezone (should preserve)
        mock_template.async_render = MagicMock(return_value="2025-12-25T16:00:00+09:00")

        result = await parser.resolve_template(mock_template)

        assert result.hour == 16
        assert result.tzinfo is not None
