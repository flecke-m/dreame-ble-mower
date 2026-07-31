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


async def _connect_with_pairing(ble_device):  # type: ignore[return-type]  # noqa: F821
    """Connect to the mower, establish pairing/bonding, then return the client."""
    from bleak import BleakClient, BleakError

    if HAS_RETRY_CONNECTOR:
        client = await establish_connection(
            BleakClient, ble_device, str(ble_device.address), max_attempts=4
        )
    else:
        _LOGGER.warning(
            "bleak_retry_connector unavailable — connecting with bare bleak (no retry)"
        )
        client = BleakClient(str(ble_device.address), timeout=15.0)
        await client.connect()

    # ------------------------------------------------------------------
    # BLE Pairing / Bonding  (ROOT CAUSE for "all entities undefined")
    # ------------------------------------------------------------------
    # The mower requires authentication before it sends meaningful push
    # notifications or accepts writes on command characteristics. Without
    # bonding the connection succeeds at L2CAP level but all entity values
    # stay undefined because encrypted handles reject unauthenticated traffic.
    _LOGGER.info("Attempting BLE pairing/bonding for %s …", ble_device.address)
    try:
        paired = await client.pair(protection_key_used=True, confirm_used=True)
        _LOGGER.info("BLE pairing successful — result=%s", paired)
    except BleakError as err:
        err_msg = str(err).lower()
        # Some devices are already bonded from a previous run — that's fine
        if "already" in err_msg or "paired" in err_msg or "bonded" in err_msg:
            _LOGGER.info("Device already paired/bonded (OK): %s", err)
        else:
            _LOGGER.warning(
                "BLE pairing returned error (may still work on first connect): %s", err
            )
    except Exception as err:
        # Some bleak versions or backends don't support pair() — continue anyway
        _LOGGER.debug("pair() not available or unexpected error, continuing: %s", err)

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
        client = await _connect_with_pairing(ble_device)
    except BleakNotFoundError:
        raise ConfigEntryNotReady(
            f"Dreame Mower at {mac_address} disappeared from BLE scan results"
        )
    except Exception as err:
        raise ConfigEntryNotReady(
            f"Failed to connect + pair to Dreame Mower at {mac_address}: {err}"
        ) from err

    _LOGGER.info("BLE connection + pairing established for %s", mac_address)

    protocol = DreameBLEProtocol(client)

    # Discover GATT characteristics by UUID so we don't depend on hardcoded ATT
    # handle numbers that shift between firmware versions. Falls back to the
    # known handles if discovery finds fewer than expected characteristics.
    await protocol.discover_characteristics()

    # Subscribe to mower push notifications BEFORE the first refresh so
    # incoming responses are routed to pending futures.
    await protocol.start_notifications()

    coordinator = DreameBleCoordinator(hass, protocol)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload all platforms and disconnect BLE client."""
    coordinator = hass.data[DOMAIN].pop(entry.entry_id, None)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if coordinator:
        client = coordinator._protocol._client
        try:
            if client.is_connected:
                await client.disconnect()
            _LOGGER.info(
                "BLE client disconnected on unload for %s", entry.data[CONF_ADDRESS]
            )
        except Exception as err:
            _LOGGER.warning("Error disconnecting BLE client on unload: %s", err)

    return unload_ok
