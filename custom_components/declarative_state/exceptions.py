"""Custom exceptions for Declarative State."""


class DeclarativeStateError(Exception):
    """Base exception for declarative state."""


class TimeParseError(DeclarativeStateError):
    """Error parsing time format."""


class StateCalculationError(DeclarativeStateError):
    """Error calculating states."""
