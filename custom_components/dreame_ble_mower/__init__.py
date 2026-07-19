"""Dreame Mower local BLE component."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MAC
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import async_setup_component

from .const import DOMAIN, PLATFORMS
from .coordinator import DreameBleCoordinator
from .protocol import DreameBLEProtocol

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["lawn_mower", "sensor", "binary_sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Dreame Mower BLE from a config entry."""
    _LOGGER.info("Setting up local BLE connection for %s", entry.data[CONF_MAC])

    mac_address = entry.data[CONF_MAC]
    
    try:
        # Establish bleak client directly to the mower
        from bleak import BleakClient
        async with BleakClient(mac_address) as client:
            if await client.is_connected:
                protocol = DreameBLEProtocol(client)
                
                coordinator = DreameBleCoordinator(hass, protocol)
                await coordinator.async_config_entry_first_refresh()

                hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
                
                await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
                
                return True
            
    except Exception as err:
        _LOGGER.error("Could not connect to Dreame mower at %s: %s", mac_address, err)
        raise

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        # Stop bleak client loop to release bluetooth adapter 
        try:
            await coordinator._protocol._client.disconnect()
        except Exception: pass
    return unload_ok
