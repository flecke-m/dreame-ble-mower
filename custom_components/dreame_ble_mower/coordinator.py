"""Data coordinator that bridges raw BLE packets into Home Assistant state dictionaries."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, SCAN_INTERVAL_SEC
from .protocol import (
    DREAME_HANDLE_COMMANDS_TASKS,
    DREAME_HANDLE_DEVICE_STATUS,
    DreameBLEProtocol,
)

_LOGGER = logging.getLogger(__name__)


class DreameBleCoordinator(DataUpdateCoordinator):
    """Manages the connection to the local mower and updates HA entities."""

    def __init__(self, hass: HomeAssistant, protocol: DreameBLEProtocol) -> None:
        super().__init__(
            hass, 
            _LOGGER, 
            name=f"{DOMAIN}-coordinator", 
            update_interval=timedelta(seconds=SCAN_INTERVAL_SEC)
        )
        self._protocol = protocol

        # State fields derived from the Cloud integration for HA entities
        self._state = {
            "battery_percent": 0,
            "charging_status": "not_docked",
            "activity": "no_status",
            "mowing_zone": 0,
        }

    async def _async_update_data(self):
        """Poll the mower for current state and update our dict."""
        try:
            # Request Battery State (CFG) via device-status handle -> 'BAT' key
            battery_resp = await self._protocol.read_status(
                DREAME_HANDLE_DEVICE_STATUS, "CFG"
            )

            # Request Task Status via commands handle -> 'TASK' key
            task_resp = await self._protocol.read_status(
                DREAME_HANDLE_COMMANDS_TASKS, "TASK"
            )

            return self._parse_responses(battery_resp, task_resp)
        except Exception as ex:
            raise UpdateFailed(f"Failed to update mower state from BLE: {ex}") from ex

    def _parse_responses(self, cfg_data: dict | None, task_data: dict | None) -> dict:
        """Translate raw JSON payloads into HA-compatible values (mirroring cloud_device.py mappings)."""
        self._state["battery_percent"] = 0
        self._state["charging_status"] = "not_docked"
        self._state["activity"] = "no_status"

        if cfg_data and "BAT" in cfg_data.get("d", {}):
            bat_arr = cfg_data["d"]["BAT"]
            # BAT array layout: [voltage, percent, charge_state, err_code, total_mins, last_cycle]
            self._state["battery_percent"] = bat_arr[1]
            self._state["charging_status"] = "charging" if bat_arr[2] == 1 else ("docked" if bat_arr[2] == 0 else "not_docked")

        # Parse task execution push (m:"p", exe:true/false) to map activity correctly.
        if task_data and "exe" in task_data.get("d", {}):
            is_working = task_data["d"]["exe"]
            op_code = task_data["d"].get("o", 0)

            if is_working:
                self._state["activity"] = "mowing" if op_code == 207 else "mapping"
                self._state["mowing_zone"] = task_data.get("d", {}).get("idx", 0)
            elif op_code == 202 or op_code == 100:
                self._state["activity"] = "paused"
            elif op_code == 200:
                self._state["activity"] = "returning_to_station_to_charge"

        return self._state.copy()
