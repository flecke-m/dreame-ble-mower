"""Dreame Mower local BLE component."""
from __future__ import annotations

import logging

from homeassistant.components.bluetooth import async_ble_device_from_address
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN, PLATFORMS
from .coordinator import DreameBleCoordinator
from .protocol import DreameBLEProtocol

_LOGGER = logging.getLogger(__name__)

# Defer all heavy third-party imports (bleak, bleak_retry_connector) until they
# are actually needed. Resolved lazily, exactly once, by _resolve_bleak().
HAS_RETRY_CONNECTOR = None  # Resolved lazily in _resolve_bleak()


class _BleakNotFoundFallback(Exception):
    """Local stand-in for bleak_retry_connector.BleakNotFoundError.

    Only meaningful as an exception type; nothing raises it when the real
    package is missing, so it never shadows genuine connection errors.
    """


_RETRY_NOT_FOUND_ERROR = _BleakNotFoundFallback
_RETRY_ESTABLISH = None


def _resolve_bleak():
    """Lazily resolve bleak_retry_connector exactly once; safe to call again.

    Avoids blocking HA's event loop at module-load time while still keeping
    the try/except fallback logic intact. Repeated calls (from both
    async_setup_entry and _connect) always return bound values.
    """
    global HAS_RETRY_CONNECTOR, _RETRY_NOT_FOUND_ERROR, _RETRY_ESTABLISH
    if HAS_RETRY_CONNECTOR is not None:
        return _RETRY_NOT_FOUND_ERROR, _RETRY_ESTABLISH

    try:
        from bleak_retry_connector import (
            BleakNotFoundError,
            establish_connection,
        )
    except ImportError:
        HAS_RETRY_CONNECTOR = False
        _LOGGER.warning(
            "bleak_retry_connector unavailable — falling back to bare bleak "
            "with no retry"
        )
        return _RETRY_NOT_FOUND_ERROR, _RETRY_ESTABLISH

    HAS_RETRY_CONNECTOR = True
    _RETRY_NOT_FOUND_ERROR = BleakNotFoundError
    _RETRY_ESTABLISH = establish_connection
    return _RETRY_NOT_FOUND_ERROR, _RETRY_ESTABLISH


async def _connect(ble_device):  # type: ignore[return-type]
    """Connect to the mower (pure BLE connect — no pairing, no SMP).

    All four PCAPs and the decompiled app confirm the mower opens
    unencrypted GATT with zero LE pairing — a previous iteration tried
    an app-level PIN/bond flow that was an assumption, not evidence.
    """
    from bleak import BleakError

    if HAS_RETRY_CONNECTOR is None:
        _resolve_bleak()

    if HAS_RETRY_CONNECTOR:
        _, establish_connection = _resolve_bleak()
        from bleak_retry_connector import BleakClientWithServiceCache

        client = await establish_connection(
            BleakClientWithServiceCache, ble_device, str(ble_device.address), max_attempts=4
        )
    else:
        from bleak import BleakClient

        _LOGGER.warning("Connecting with bare bleak (no retry)")
        client = BleakClient(str(ble_device.address), timeout=15.0)
        await client.connect()

    _LOGGER.info("BLE GATT client established for %s (no SMP pairing)", ble_device.address)
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

    # Resolve bleak (lazy import to avoid blocking HA event loop)
    BleakNotFoundError, _ = _resolve_bleak()

    try:
        client = await _connect(ble_device)
    except BleakNotFoundError:
        raise ConfigEntryNotReady(
            f"Dreame Mower at {mac_address} disappeared from BLE scan results"
        )
    except Exception as err:
        raise ConfigEntryNotReady(
            f"Failed to connect to Dreame Mower at {mac_address}: {err}"
        ) from err

    _LOGGER.info("BLE GATT connection established for %s", mac_address)

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
