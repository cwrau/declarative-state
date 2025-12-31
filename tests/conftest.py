"""Pytest fixtures for declarative_state tests."""
import pytest


@pytest.fixture
def sample_config():
    """Sample configuration fixture."""
    return {
        "name": "test_sensor",
        "lookahead": 2,
        "error_handling": "ignore",
        "states": [
            {
                "state": "on",
                "start": "16:00",
                "end": "22:00",
            },
            {
                "state": "off",
                "start": "22:00",
                "end": "16:00",
            },
        ],
    }
