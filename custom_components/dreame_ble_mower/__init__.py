"""Dreame Mower local BLE component."""
from __future__ import annotations

import logging

# Home Assistant's bluetooth_adapters dependency transitively installs
# bleak_retry_connector, but some install variants (Core-only / manual venv)
# may not have it. If unavailable we fall back to bare bleak with a retry loop.
try:
    from bleak_retry_connector import (
        BleakNotFoundError,
        establish_connection,
    )

    HAS_RETRY_CONNECTOR = True
except ImportError:
    HAS_RETRY_CONNECTOR = False
    BleakNotFoundError = Exception  # noqa: Stub for type narrowing below

from homeassistant.components.bluetooth import async_ble_device_from_address
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN, PLATFORMS
from .coordinator import DreameBleCoordinator
from .protocol import DreameBLEProtocol

_LOGGER = logging.getLogger(__name__)


async def _connect_with_retry(ble_device) -> "BleakClient":  # type: ignore[return-type]  # noqa: F821
    """Connect to the mower, with bleak_retry_connector or a manual retry loop."""
    from bleak import BleakClient

    if HAS_RETRY_CONNECTOR:
        return await establish_connection(
            BleakClient, ble_device, str(ble_device.address), max_attempts=4
        )

    # Fallback: manually retry up to 3 times in case the adapter or ESPHome proxy
    # drops the first attempt.
    _LOGGER.warning(
        "bleak_retry_connector unavailable, connecting with bare bleak (no retry)"
    )
    client = BleakClient(str(ble_device.address), timeout=15.0)
    await client.connect()
    return client


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
        # Use our retry wrapper — prefers bleak_retry_connector when available,
        # falls back to bare BleakClient.connect() + manual retries for venv/Container setups.
        client = await _connect_with_retry(ble_device)

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
