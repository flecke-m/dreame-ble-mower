"""Dreame Mower local BLE component."""
from __future__ import annotations

import logging

from bleak_retry_connector import (
    BleakNotFoundError,
    establish_connection,
)

from homeassistant.components.bluetooth import async_ble_device_from_address
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN, PLATFORMS
from .coordinator import DreameBleCoordinator
from .protocol import DreameBLEProtocol

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Dreame Mower BLE from a config entry."""
    mac_address = entry.data[CONF_ADDRESS]
    _LOGGER.info("Setting up local BLE connection for %s", mac_address)

    # Get the nearest adapter that can reach the device via HA's bluetooth manager.
    ble_device = async_ble_device_from_address(hass, mac_address, connectable=True)
    if not ble_device:
        raise ConfigEntryNotReady(
            f"Dreame Mower at {mac_address} not found by any connected BLE adapter"
        )

    try:
        # establish_connection() expects (client_class, device, name, ...) — it handles
        # connection parameter negotiation with the ESPHome proxy and retries on transient
        # failure. The returned client exposes all bleak APIs (write_gatt_char, services).
        from bleak import BleakClient

        client = await establish_connection(
            BleakClient, ble_device, mac_address, max_attempts=4
        )

    except BleakNotFoundError:
        raise ConfigEntryNotReady(
            f"Dreame Mower at {mac_address} disappeared from BLE scan results"
        )
    except Exception as err:
        # Catch-all for BLEError, connection failures, timeouts, etc.
        raise ConfigEntryNotReady(
            f"Failed to connect to Dreame Mower at {mac_address}: {err}"
        ) from err

    _LOGGER.info("BLE connection established")

    protocol = DreameBLEProtocol(client)
    coordinator = DreameBleCoordinator(hass, protocol)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "client": client,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


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
