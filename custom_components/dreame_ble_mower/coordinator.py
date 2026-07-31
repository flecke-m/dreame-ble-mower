"""Data coordinator that bridges raw BLE packets into Home Assistant state dictionaries."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from threading import Lock

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, SCAN_INTERVAL_SEC
from .protocol import DreameBLEProtocol

_LOGGER = logging.getLogger(__name__)


class DreameBleCoordinator(DataUpdateCoordinator):
    """Manages the connection to the local mower and updates HA entities.

    The mower communicates primarily through ASYNC PUSH notifications rather than
    synchronous request/response. This coordinator:
      1. Requests state at startup/interval (fire-and-forget — doesn't block)
      2. Collects actual data from unsolicited push notifications via on_status_update
      3. Reports the latest known state to HA entities
    """

    def __init__(self, hass: HomeAssistant, protocol: DreameBLEProtocol) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-coordinator",
            update_interval=timedelta(seconds=SCAN_INTERVAL_SEC),
        )
        self._protocol = protocol

        # Thread-safe state dict — on_status_update runs in bleak's callback thread
        self._lock = Lock()
        self._state: dict = {
            "battery_percent": 0,
            "charging_status": "not_docked",
            "activity": "no_status",
            "mowing_zone": 0,
        }

        # Track whether we've ever received a valid push from the mower
        self._ever_received = False
        self._raw_notifications: list = []

        # Wire up the protocol's push callback to our state machine
        self._protocol.on_status_update = self._handle_push

    def _handle_push(self, parsed: dict):
        """Process an unsolicited status push from the mower.

        Called from bleak's notification callback thread — must be fast and non-blocking.
        """
        self._ever_received = True

        # Keep a rolling buffer of the last 10 notifications for debugging
        with self._lock:
            self._raw_notifications.append(parsed)
            if len(self._raw_notifications) > 10:
                self._raw_notifications.pop(0)

        msg_type = parsed.get("m")
        q_id = parsed.get("q")
        data = parsed.get("d", {})

        _LOGGER.debug(
            "Push notification — m=%s, q=%s, keys=%s",
            msg_type,
            q_id,
            list(parsed.keys()),
        )

        # --- Decode battery state (CFG push with BAT array) ---
        if isinstance(data, dict) and "BAT" in data:
            bat_arr = data["BAT"]
            if isinstance(bat_arr, (list, tuple)) and len(bat_arr) >= 3:
                battery_pct = bat_arr[1]
                charge_state = bat_arr[2]
                charging = (
                    "charging" if charge_state == 1
                    else ("docked" if charge_state == 0
                          else "not_docked")
                )
                with self._lock:
                    self._state["battery_percent"] = battery_pct
                    self._state["charging_status"] = charging
                _LOGGER.info(
                    "Battery update — %d%%, status=%s",
                    battery_pct,
                    charging,
                )

        # --- Decode task/activity state ---
        if isinstance(data, dict) and "exe" in data:
            is_working = data["exe"]
            op_code = data.get("o", 0)

            with self._lock:
                if is_working:
                    self._state["activity"] = (
                        "mowing" if op_code == 207 else "mapping"
                    )
                    self._state["mowing_zone"] = data.get("idx", 0)
                elif op_code in (202, 100):
                    self._state["activity"] = "paused"
                elif op_code == 200:
                    self._state["activity"] = (
                        "returning_to_station_to_charge"
                    )

        # Trigger HA entity refresh without blocking the callback thread
        asyncio.run_coroutine_threadsafe(
            self.__async_request_refresh(),
            self.hass.loop,
        )

    async def __async_request_refresh(self):
        """Lightweight way to push latest state to entities immediately."""
        await self.async_request_refresh()

    async def _async_update_data(self):
        """Poll the mower for current state.

        IMPORTANT: These are send-and-forget requests. The mower doesn't send
        synchronous responses matching our q-IDs — it pushes state updates
        asynchronously through the notification handle.
        We return whatever state we've collected from push notifications so far.
        """
        try:
            # Fire-and-forget GET for battery/config data
            await self._protocol.read_status(
                self._protocol.handle_device_status, "CFG"
            )

            # Fire-and-forget GET for task status
            await self._protocol.read_status(
                self._protocol.handle_commands_tasks, "TASK"
            )

        except Exception as ex:
            _LOGGER.warning("Failed to send status request: %s", ex)
            # Don't raise — we still return cached state from pushes

        with self._lock:
            current = self._state.copy()

        if not self._ever_received:
            _LOGGER.info(
                "No push notifications received yet. "
                "Current state is default/empty — waiting for mower to push."
            )

        return current
