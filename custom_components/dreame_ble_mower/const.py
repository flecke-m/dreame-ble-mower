"""Constants for the local Dreame Mower BLE integration."""

from __future__ import annotations

import logging

DOMAIN = "dreame_ble_mower"
LOGGER = logging.getLogger(__name__)
CONF_MAC_ADDRESS = "mac_address"

# Update frequency for the coordinator (seconds) 
SCAN_INTERVAL_SEC = 15

# Platforms this integration provides
PLATFORMS: list[str] = ["lawn_mower", "sensor"]

# Standard entity registries mapped to our discovered JSON keys.
ATTR_BATTERY_PERCENT = "battery_percent"
ATTR_CHARGING_STATUS = "charging_status"
ATTR_MOWER_ACTIVITY  = "activity"
ATTR_SERIAL_NUMBER   = "sn"
ATTR_FIRMWARE        = "fw"
