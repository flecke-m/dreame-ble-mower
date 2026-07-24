"""Dreame Mower local BLE component."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .coordinator import DreameBleCoordinator
from .protocol import DreameBLEProtocol

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Dreame Mower BLE from a config entry."""
    mac_address = entry.data[CONF_ADDRESS]
    _LOGGER.info("Setting up local BLE connection for %s", mac_address)

    client = None
    had_error = False

    try:
        from bleak import BleakClient

        # Create client outside context manager so it survives beyond setup.
        client = BleakClient(mac_address, timeout=15.0)
        await client.connect()

        if not await client.is_connected:
            raise ConnectionError(
                f"Bleak reported connect success but is_connected=False for {mac_address}"
            )

        _LOGGER.info("BLE connection established, discovered %d services", len(client.services))

        protocol = DreameBLEProtocol(client)
        coordinator = DreameBleCoordinator(hass, protocol)
        await coordinator.async_config_entry_first_refresh()

        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
            "coordinator": coordinator,
            "client": client,
        }

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        return True

    except Exception as err:
        _LOGGER.error("Could not connect to Dreame mower at %s: %s", mac_address, err)
        had_error = True
        raise

    finally:
        # Only disconnect on the error path so a working client persists.
        if had_error and client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload all platforms and disconnect BLE client."""
    data = hass.data[DOMAIN].pop(entry.entry_id, None)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if data:
        client = data.get("client")
        if client is not None:
            try:
                if await client.is_connected:
                    await client.disconnect()
                _LOGGER.info(
                    "BLE client disconnected on unload for %s", entry.data[CONF_ADDRESS]
                )
            except Exception as err:
                _LOGGER.warning("Error disconnecting BLE client on unload: %s", err)

    return unload_ok
