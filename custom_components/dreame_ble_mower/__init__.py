"""Dreame Mower local BLE component."""
from __future__ import annotations

import logging

# Defer all heavy third-party imports (bleak, bleak_retry_connector) until they
# are actually needed inside the async setup function. Importing them here would
# block HA's main event loop during loader.import_module.
HAS_RETRY_CONNECTOR = None  # Resolved lazily in _resolve_bleak()

from homeassistant.components.bluetooth import async_ble_device_from_address
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN, PLATFORMS
from .coordinator import DreameBleCoordinator
from .protocol import DreameBLEProtocol

_LOGGER = logging.getLogger(__name__)


def _resolve_bleak():
    """Lazily resolve bleak + bleak_retry_connector on first call.

    This avoids blocking HA's event loop at module-load time while still keeping
    the try/except fallback logic intact.
    """
    global HAS_RETRY_CONNECTOR  # noqa: PLW0603
    if HAS_RETRY_CONNECTOR is None:
        try:
            from bleak_retry_connector import BleakNotFoundError, establish_connection

            BleakNotFoundError()  # noqa: B018 – sanity-instantiate to catch broken installs early
            HAS_RETRY_CONNECTOR = True
        except ImportError:
            _LOGGER.warning(
                "bleak_retry_connector unavailable — falling back to bare bleak with no retry"
            )
            HAS_RETRY_CONNECTOR = False

    return BleakNotFoundError, establish_connection


async def _connect_with_pairing(ble_device):  # type: ignore[return-type]
    """Connect to the mower, establish pairing/bonding, then return the client."""
    from bleak import BleakClient, BleakError

    if HAS_RETRY_CONNECTOR is None:
        _resolve_bleak()

    if HAS_RETRY_CONNECTOR:
        _, establish_connection = _resolve_bleak()
        client = await establish_connection(
            BleakClient, ble_device, str(ble_device.address), max_attempts=4
        )
    else:
        _LOGGER.warning("Connecting with bare bleak (no retry)")
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

    # Resolve bleak (lazy import to avoid blocking HA event loop)
    BleakNotFoundError, _ = _resolve_bleak()

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
