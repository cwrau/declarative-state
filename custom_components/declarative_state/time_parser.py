"""Time format parsing and cron conversion."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from croniter import croniter
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util

from .exceptions import TimeParseError
from .models import TimeSpec

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class TimeParser:
    """Parse various time formats into cron expressions."""

    # Regex patterns
    TIME_ONLY_PATTERN = re.compile(r"^(\d{1,2}):(\d{2})$")  # HH:MM
    HOUR_ONLY_PATTERN = re.compile(r"^(\d{1,2})$")  # HH
    ISO8601_PATTERN = re.compile(
        r"^(\*|\d{4})-(\*|\d{1,2})-(\*|\d{1,2})(?:T(\d{1,2})?(?::(\d{2}))?(?::(\d{2}))?)?$"
    )

    def __init__(self, hass: HomeAssistant):
        """Initialize time parser."""
        self.hass = hass

    def parse(self, time_value: str | Template) -> TimeSpec:
        """Parse a time value into a TimeSpec."""
        if isinstance(time_value, Template):
            return TimeSpec(raw=time_value, is_template=True)

        # Try different formats - each parser returns None if no match
        try:
            if result := self._parse_time_only(time_value):
                return result
            elif result := self._parse_hour_only(time_value):
                return result
            elif result := self._parse_iso8601(time_value):
                return result
            else:
                raise TimeParseError(f"Unrecognized time format: {time_value}")
        except TimeParseError:
            raise
        except Exception as err:
            raise TimeParseError(
                f"Failed to parse time '{time_value}': {err}"
            ) from err

    def _parse_time_only(self, time_str: str) -> TimeSpec | None:
        """Parse HH:MM format to cron. Returns None if format doesn't match."""
        if not (match := self.TIME_ONLY_PATTERN.match(time_str)):
            return None

        hour, minute = match.groups()
        # Cron format: minute hour day month day_of_week
        cron = f"{minute} {hour} * * *"
        return TimeSpec(raw=time_str, is_template=False, parsed_cron=cron)

    def _parse_hour_only(self, hour_str: str) -> TimeSpec | None:
        """Parse HH format to cron. Returns None if format doesn't match."""
        if not (match := self.HOUR_ONLY_PATTERN.match(hour_str)):
            return None

        hour = match.group(1)
        # Default to minute 0
        cron = f"0 {hour} * * *"
        return TimeSpec(raw=hour_str, is_template=False, parsed_cron=cron)

    def _parse_iso8601(self, iso_str: str) -> TimeSpec | None:
        """Parse ISO8601 with wildcards to cron. Returns None if format doesn't match."""
        if not (match := self.ISO8601_PATTERN.match(iso_str)):
            return None

        year, month, day, hour, minute, second = match.groups()

        # Convert to cron (minute hour day month day_of_week)
        # Wildcards stay as *, specific values are used
        # Strip leading zeros by converting to int and back
        minute = str(int(minute)) if minute else "0"
        hour = str(int(hour)) if hour else "0"
        day = "*" if day == "*" else str(int(day))
        month = "*" if month == "*" else str(int(month))

        cron = f"{minute} {hour} {day} {month} *"
        return TimeSpec(raw=iso_str, is_template=False, parsed_cron=cron)

    async def resolve_template(self, template: Template) -> datetime:
        """Resolve a template to a datetime."""
        try:
            result = template.async_render()

            # Handle various return types
            if result is None:
                raise TimeParseError(
                    "Template returned None - entity may not exist or is unavailable. "
                    "Check that all entities referenced in the template exist and have valid states."
                )
            elif isinstance(result, datetime):
                # Ensure datetime is timezone-aware
                if result.tzinfo is None:
                    result = dt_util.as_local(result)
                return result
            elif isinstance(result, str):
                # Try parsing as ISO8601
                parsed = dt_util.parse_datetime(result)
                if parsed is None:
                    raise TimeParseError(f"Unable to parse datetime from: {result}")
                # Ensure parsed datetime is timezone-aware
                if parsed.tzinfo is None:
                    parsed = dt_util.as_local(parsed)
                return parsed
            else:
                raise TimeParseError(
                    f"Template returned unexpected type: {type(result)}"
                )
        except TimeParseError:
            raise
        except Exception as err:
            raise TimeParseError(f"Failed to resolve template: {err}") from err

    def get_next_occurrence(self, time_spec: TimeSpec, after: datetime) -> datetime:
        """Get next occurrence of a time spec after a given datetime."""
        if time_spec.is_template:
            # Templates are resolved once, not recurring
            raise ValueError("Cannot get next occurrence of template")

        # Ensure after is timezone-aware in HA's configured timezone
        if after.tzinfo is None:
            after = dt_util.as_local(after)

        # Get Home Assistant's configured timezone (not DEFAULT_TIME_ZONE which may be UTC)
        ha_tz = dt_util.get_time_zone(self.hass.config.time_zone) if hasattr(self.hass, 'config') else after.tzinfo

        # Convert to HA timezone if needed
        if ha_tz and after.tzinfo != ha_tz:
            after = after.astimezone(ha_tz)

        # Use croniter to find next occurrence
        cron = croniter(time_spec.parsed_cron, after)
        next_dt = cron.get_next(datetime)

        # Ensure result is in HA timezone
        if next_dt.tzinfo is None or next_dt.tzinfo != ha_tz:
            if ha_tz:
                next_dt = next_dt.replace(tzinfo=ha_tz) if next_dt.tzinfo is None else next_dt.astimezone(ha_tz)
            else:
                next_dt = dt_util.as_local(next_dt)

        return next_dt

    def get_prev_occurrence(self, time_spec: TimeSpec, before: datetime) -> datetime:
        """Get previous occurrence of a time spec before a given datetime."""
        if time_spec.is_template:
            # Templates are resolved once, not recurring
            raise ValueError("Cannot get previous occurrence of template")

        # Ensure before is timezone-aware in HA's configured timezone
        if before.tzinfo is None:
            before = dt_util.as_local(before)

        # Get Home Assistant's configured timezone
        ha_tz = dt_util.get_time_zone(self.hass.config.time_zone) if hasattr(self.hass, 'config') else before.tzinfo

        # Convert to HA timezone if needed
        if ha_tz and before.tzinfo != ha_tz:
            before = before.astimezone(ha_tz)

        # Use croniter to find previous occurrence
        cron = croniter(time_spec.parsed_cron, before)
        prev_dt = cron.get_prev(datetime)

        # Ensure result is in HA timezone
        if prev_dt.tzinfo is None or prev_dt.tzinfo != ha_tz:
            if ha_tz:
                prev_dt = prev_dt.replace(tzinfo=ha_tz) if prev_dt.tzinfo is None else prev_dt.astimezone(ha_tz)
            else:
                prev_dt = dt_util.as_local(prev_dt)

        return prev_dt

    async def get_time_at_or_before(
        self, time_spec: TimeSpec, at_time: datetime
    ) -> datetime | None:
        """Get the time value at or before a given datetime (unified for templates and crons)."""
        if time_spec.is_template:
            # Resolve template - it gives one specific datetime
            resolved = await self.resolve_template(time_spec.raw)
            # Only return if it's at or before the search time
            return resolved if resolved <= at_time else None
        else:
            # For cron, get most recent occurrence at or before at_time
            # Add 1 microsecond so get_prev includes times exactly at at_time
            return self.get_prev_occurrence(time_spec, at_time + timedelta(microseconds=1))

    async def get_time_after(
        self, time_spec: TimeSpec, after_time: datetime
    ) -> datetime:
        """Get the time value after a given datetime (unified for templates and crons)."""
        if time_spec.is_template:
            # Resolve template - it gives one specific datetime
            resolved = await self.resolve_template(time_spec.raw)

            # If template result is not after requested time, adjust it
            # Templates like sun_next_dawn return current next occurrence,
            # but we might need a future occurrence
            if resolved <= after_time:
                # Assume daily pattern and add days until we're past after_time
                days_to_add = 1
                max_iterations = 400  # Prevent infinite loop (covers ~1 year)
                while resolved <= after_time and days_to_add < max_iterations:
                    resolved = resolved + timedelta(days=days_to_add)
                    days_to_add += 1

            return resolved
        else:
            # For cron, get next occurrence after the given time
            return self.get_next_occurrence(time_spec, after_time)
