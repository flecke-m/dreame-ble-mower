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
ATTR_MOWER_ACTIVITY = "activity"
ATTR_SERIAL_NUMBER = "sn"
ATTR_FIRMWARE = "fw"

# --- GATT identity (byte-verified from newBLElog.pcap frame 673) ---
# The mower's single 128-bit data service, handles 0x0014..0x003a.
MOWER_SERVICE_UUID = "743345ba-72ea-4343-bd74-4b4c16040000"

# Confirmed ATT handles (see protocol.py module docstring).
CHAR_HANDLE_COMMAND = 0x0020   # write-with-response command channel
CHAR_HANDLE_CCCD    = 0x0021   # its CCCD (notify subscribe)
CHAR_HANDLE_AUX     = 0x0023   # auxiliary write (e.g. time sync)
CHAR_HANDLE_DATA    = 0x001D   # data read / notification target

# Default device MAC from the reference captures (DreameInnova G2422052...).
DEFAULT_MOWER_MAC = "10:06:48:A0:E6:95"
