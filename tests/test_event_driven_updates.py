"""Tests for event-driven updates."""
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import Event, State
from homeassistant.helpers.template import Template
from custom_components.declarative_state.sensor import async_setup_platform


@pytest.fixture
def mock_hass():
    """Mock Home Assistant instance with state tracking."""
    hass = MagicMock()
    hass.states = MagicMock()

    # Track event listeners
    hass.tracked_entities = []

    def mock_track_state(entity_ids, callback):
        hass.tracked_entities.extend(entity_ids)
        return MagicMock()  # Return cancellation function

    hass.async_create_task = MagicMock()
    return hass


@pytest.mark.asyncio
async def test_tracks_template_entities(mock_hass):
    """Test that entities referenced in templates are tracked."""
    config = {
        "name": "test_sensor",
        "lookahead": 0,
        "error_handling": "ignore",
        "states": [
            {
                "state": "off",
                "start": "{{ states('sensor.sun_next_dawn') | as_datetime }}",
                "end": "{{ states('sensor.sun_next_dusk') | as_datetime }}",
            }
        ],
    }

    async_add_entities = MagicMock()

    # Mock RenderInfo to return tracked entities
    mock_render_info_start = MagicMock()
    mock_render_info_start.entities = {"sensor.sun_next_dawn"}

    mock_render_info_end = MagicMock()
    mock_render_info_end.entities = {"sensor.sun_next_dusk"}

    with patch(
        "custom_components.declarative_state.sensor.async_track_state_change_event"
    ) as mock_track:
        with patch(
            "custom_components.declarative_state.sensor.DeclarativeStateCoordinator"
        ) as mock_coordinator_class:
            with patch(
                "custom_components.declarative_state.sensor.Template"
            ) as mock_template_class:
                # Mock coordinator
                mock_coordinator = MagicMock()
                mock_coordinator.async_config_entry_first_refresh = AsyncMock()
                mock_coordinator.data = []
                mock_coordinator_class.return_value = mock_coordinator

                # Mock Template to return proper RenderInfo
                call_count = [0]
                def create_template(template_str, hass):
                    mock_template = MagicMock(spec=Template)
                    # Alternate between start and end templates
                    if call_count[0] % 2 == 0:
                        mock_template.async_render_to_info.return_value = mock_render_info_start
                    else:
                        mock_template.async_render_to_info.return_value = mock_render_info_end
                    call_count[0] += 1
                    return mock_template

                mock_template_class.side_effect = create_template

                await async_setup_platform(mock_hass, config, async_add_entities)

                # Verify that state change tracking was set up
                assert mock_track.called
                call_args = mock_track.call_args
                tracked_entities = call_args[0][1]  # Second positional arg

                # Should track sun sensor entities
                assert "sensor.sun_next_dawn" in tracked_entities
                assert "sensor.sun_next_dusk" in tracked_entities


@pytest.mark.asyncio
async def test_tracks_condition_entities(mock_hass):
    """Test that entities referenced in conditions are tracked."""
    config = {
        "name": "test_sensor",
        "lookahead": 0,
        "error_handling": "ignore",
        "states": [
            {
                "state": "off",
                "start": "01:00",
                "end": "06:00",
                "conditions": [
                    {"condition": "state", "entity_id": "input_boolean.sleeping", "state": "on"}
                ],
            }
        ],
    }

    async_add_entities = MagicMock()

    with patch(
        "custom_components.declarative_state.sensor.async_track_state_change_event"
    ) as mock_track:
        with patch(
            "custom_components.declarative_state.sensor.DeclarativeStateCoordinator"
        ) as mock_coordinator_class:
            # Mock coordinator
            mock_coordinator = MagicMock()
            mock_coordinator.async_config_entry_first_refresh = AsyncMock()
            mock_coordinator.data = []
            mock_coordinator_class.return_value = mock_coordinator

            await async_setup_platform(mock_hass, config, async_add_entities)

            # Verify that state change tracking was set up
            assert mock_track.called
            call_args = mock_track.call_args
            tracked_entities = call_args[0][1]  # Second positional arg

            # Should track condition entity
            assert "input_boolean.sleeping" in tracked_entities


@pytest.mark.asyncio
async def test_coordinator_refreshes_on_entity_change(mock_hass):
    """Test that coordinator refreshes when tracked entity changes."""
    config = {
        "name": "test_sensor",
        "lookahead": 0,
        "error_handling": "ignore",
        "states": [
            {
                "state": "off",
                "start": "01:00",
                "end": "06:00",
                "conditions": [
                    {"condition": "state", "entity_id": "input_boolean.sleeping", "state": "on"}
                ],
            }
        ],
    }

    async_add_entities = MagicMock()
    callback_func = None

    def capture_callback(hass, entities, callback):
        nonlocal callback_func
        callback_func = callback
        return MagicMock()

    with patch(
        "custom_components.declarative_state.sensor.async_track_state_change_event",
        side_effect=capture_callback,
    ):
        with patch(
            "custom_components.declarative_state.sensor.DeclarativeStateCoordinator"
        ) as mock_coordinator_class:
            # Mock coordinator
            mock_coordinator = MagicMock()
            mock_coordinator.async_config_entry_first_refresh = AsyncMock()
            mock_coordinator.async_refresh = AsyncMock()
            mock_coordinator.data = []
            mock_coordinator_class.return_value = mock_coordinator

            await async_setup_platform(mock_hass, config, async_add_entities)

            # Verify callback was captured
            assert callback_func is not None

            # Simulate entity state change
            event = MagicMock(spec=Event)
            callback_func(event)

            # Verify coordinator refresh was requested
            assert mock_hass.async_create_task.called


@pytest.mark.asyncio
async def test_tracks_multiple_entities(mock_hass):
    """Test that all entities from templates and conditions are tracked."""
    config = {
        "name": "test_sensor",
        "lookahead": 0,
        "error_handling": "ignore",
        "states": [
            {
                "state": "off",
                "start": "{{ states('sensor.sun_next_dawn') | as_datetime }}",
                "end": "{{ states('sensor.sun_next_dusk') | as_datetime }}",
                "conditions": [
                    {"condition": "state", "entity_id": "input_boolean.sleeping", "state": "on"}
                ],
            },
            {
                "state": "on",
                "start": "18:00",
                "end": "22:00",
                "conditions": [
                    {"condition": "state", "entity_id": "binary_sensor.motion", "state": "on"}
                ],
            },
        ],
    }

    async_add_entities = MagicMock()

    # Mock RenderInfo to return tracked entities
    mock_render_info_start = MagicMock()
    mock_render_info_start.entities = {"sensor.sun_next_dawn"}

    mock_render_info_end = MagicMock()
    mock_render_info_end.entities = {"sensor.sun_next_dusk"}

    with patch(
        "custom_components.declarative_state.sensor.async_track_state_change_event"
    ) as mock_track:
        with patch(
            "custom_components.declarative_state.sensor.DeclarativeStateCoordinator"
        ) as mock_coordinator_class:
            with patch(
                "custom_components.declarative_state.sensor.Template"
            ) as mock_template_class:
                # Mock coordinator
                mock_coordinator = MagicMock()
                mock_coordinator.async_config_entry_first_refresh = AsyncMock()
                mock_coordinator.data = []
                mock_coordinator_class.return_value = mock_coordinator

                # Mock Template to return proper RenderInfo
                call_count = [0]
                def create_template(template_str, hass):
                    mock_template = MagicMock(spec=Template)
                    # Alternate between start and end templates
                    if call_count[0] % 2 == 0:
                        mock_template.async_render_to_info.return_value = mock_render_info_start
                    else:
                        mock_template.async_render_to_info.return_value = mock_render_info_end
                    call_count[0] += 1
                    return mock_template

                mock_template_class.side_effect = create_template

                await async_setup_platform(mock_hass, config, async_add_entities)

                # Verify that state change tracking was set up
                assert mock_track.called
                call_args = mock_track.call_args
                tracked_entities = call_args[0][1]  # Second positional arg

                # Should track all entities
                assert "sensor.sun_next_dawn" in tracked_entities
                assert "sensor.sun_next_dusk" in tracked_entities
                assert "input_boolean.sleeping" in tracked_entities
                assert "binary_sensor.motion" in tracked_entities
