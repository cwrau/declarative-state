"""Tests for sensor naming with lookahead."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.declarative_state.sensor import async_setup_platform


@pytest.fixture
def mock_hass():
    """Mock Home Assistant instance."""
    hass = MagicMock()
    return hass


@pytest.mark.asyncio
async def test_sensor_naming_lookahead_0(mock_hass):
    """Test that lookahead=0 creates only base sensor."""
    config = {
        "name": "test_sensor",
        "lookahead": 0,
        "error_handling": "ignore",
        "states": [
            {
                "state": "on",
                "start": "08:00",
                "end": "17:00",
            }
        ],
    }

    entities_added = []

    def capture_entities(entities):
        entities_added.extend(entities)

    with patch(
        "custom_components.declarative_state.sensor.DeclarativeStateCoordinator"
    ) as mock_coordinator_class:
        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator.data = []
        mock_coordinator_class.return_value = mock_coordinator

        await async_setup_platform(mock_hass, config, capture_entities)

        # Should have 1 sensor
        assert len(entities_added) == 1
        assert entities_added[0]._attr_name == "test_sensor"


@pytest.mark.asyncio
async def test_sensor_naming_lookahead_1(mock_hass):
    """Test that lookahead=1 creates base + _next sensors."""
    config = {
        "name": "test_sensor",
        "lookahead": 1,
        "error_handling": "ignore",
        "states": [
            {
                "state": "on",
                "start": "08:00",
                "end": "17:00",
            }
        ],
    }

    entities_added = []

    def capture_entities(entities):
        entities_added.extend(entities)

    with patch(
        "custom_components.declarative_state.sensor.DeclarativeStateCoordinator"
    ) as mock_coordinator_class:
        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator.data = []
        mock_coordinator_class.return_value = mock_coordinator

        await async_setup_platform(mock_hass, config, capture_entities)

        # Should have 2 sensors
        assert len(entities_added) == 2
        assert entities_added[0]._attr_name == "test_sensor"
        assert entities_added[1]._attr_name == "test_sensor_next"


@pytest.mark.asyncio
async def test_sensor_naming_lookahead_3(mock_hass):
    """Test that lookahead=3 creates base + _next + _next_1 + _next_2 sensors."""
    config = {
        "name": "test_sensor",
        "lookahead": 3,
        "error_handling": "ignore",
        "states": [
            {
                "state": "on",
                "start": "08:00",
                "end": "17:00",
            }
        ],
    }

    entities_added = []

    def capture_entities(entities):
        entities_added.extend(entities)

    with patch(
        "custom_components.declarative_state.sensor.DeclarativeStateCoordinator"
    ) as mock_coordinator_class:
        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator.data = []
        mock_coordinator_class.return_value = mock_coordinator

        await async_setup_platform(mock_hass, config, capture_entities)

        # Should have 4 sensors: base, _next, _next_1, _next_2
        assert len(entities_added) == 4
        assert entities_added[0]._attr_name == "test_sensor"
        assert entities_added[1]._attr_name == "test_sensor_next"
        assert entities_added[2]._attr_name == "test_sensor_next_1"
        assert entities_added[3]._attr_name == "test_sensor_next_2"


@pytest.mark.asyncio
async def test_sensor_naming_lookahead_5(mock_hass):
    """Test that lookahead=5 creates correctly named sensors."""
    config = {
        "name": "my_state",
        "lookahead": 5,
        "error_handling": "ignore",
        "states": [
            {
                "state": "on",
                "start": "08:00",
                "end": "17:00",
            }
        ],
    }

    entities_added = []

    def capture_entities(entities):
        entities_added.extend(entities)

    with patch(
        "custom_components.declarative_state.sensor.DeclarativeStateCoordinator"
    ) as mock_coordinator_class:
        mock_coordinator = MagicMock()
        mock_coordinator.async_config_entry_first_refresh = AsyncMock()
        mock_coordinator.data = []
        mock_coordinator_class.return_value = mock_coordinator

        await async_setup_platform(mock_hass, config, capture_entities)

        # Should have 6 sensors: base, _next, _next_1, _next_2, _next_3, _next_4
        assert len(entities_added) == 6
        assert entities_added[0]._attr_name == "my_state"
        assert entities_added[1]._attr_name == "my_state_next"
        assert entities_added[2]._attr_name == "my_state_next_1"
        assert entities_added[3]._attr_name == "my_state_next_2"
        assert entities_added[4]._attr_name == "my_state_next_3"
        assert entities_added[5]._attr_name == "my_state_next_4"
