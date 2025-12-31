"""Declarative State Integration."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.typing import ConfigType

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Declarative State integration from YAML."""
    # Import YAML configurations into config entries
    if DOMAIN in config:
        # Get sensor platform configs (handles both dict and list)
        platform_configs = config[DOMAIN]
        if not isinstance(platform_configs, list):
            platform_configs = [platform_configs]

        for platform_config in platform_configs:
            # Trigger import flow for each YAML config
            hass.async_create_task(
                hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={"source": "import"},
                    data=platform_config,
                )
            )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Declarative State from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(entry.entry_id, {})

    # Forward to sensor platform
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register options update listener
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Stop entity controller if running
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    controller = entry_data.get("controller")
    if controller:
        await controller.async_stop()

    result = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if result:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return result


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
