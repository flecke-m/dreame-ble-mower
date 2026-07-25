#!/usr/bin/env python3
"""
Dreame Mower Local BLE Protocol Bridge.
Handles low-level communication over Bluetooth Low Energy using bleak,
wrapping/unwrapping the C0 payload envelope and tracking the auto-incrementing 'q' request IDs.

Replaces the MQTT/Cloud logic from antondaubert/dreame-mower with real-time local BLE pushes.
"""

import asyncio
import json
import logging
import struct
from typing import Any, Callable, Dict, Optional

from bleak import BleakClient, BleakError

_LOGGER = logging.getLogger(__name__)

# Dreame Mower GATT handles — these are ATT handle numbers (int), not UUIDs.
DREAME_HANDLE_COMMANDS_TASKS = 0x001d   # Start mowing, park, return to base
DREAME_HANDLE_MPOS_POSITIONING = 0x0029 # Map GPS / Mower positioning state
DREAME_HANDLE_DEVICE_STATUS = 0x0023    # Timezone sync, general status/config queries
DREAME_HANDLE_NOTIFICATIONS = 0x0017    # Push notifications from mower (status/battery/events)

# BLE Opcodes discovered in PCAPs:
OP_START_MOWING = 207
OP_PARK_AT_POS = 202
OP_DOCK_RETURN = 200
OP_RESUME_CONTROL = 5

REQUEST_TIMEOUT_SEC = 5.0


class DreameBLEProtocol:
    """Low-level BLE wrapper for Dreame mower commands."""

    def __init__(self, client: BleakClient):
        self._client = client
        self._q_counter = 170  # Phone captures showed IDs starting around q=170.

        # Pending futures keyed by request ID — resolved when the mower replies
        self._pending: Dict[int, asyncio.Future] = {}

        # Callback for unsolicited push notifications (live state updates)
        self.on_status_update: Optional[Callable[[Dict[str, Any]], None]] = None

    @property
    def request_id(self) -> int:
        """Get the next auto-incrementing request ID and increment counter."""
        current_id = self._q_counter
        self._q_counter += 1
        return current_id

    # ------------------------------------------------------------------
    # Binary envelope helpers (C0 … C0)
    # ------------------------------------------------------------------

    @staticmethod
    def wrap(json_data: dict) -> bytes:
        """Encode a dictionary into the Dreame C0 envelope binary frame."""
        json_bytes = json.dumps(json_data).encode("utf-8")
        length_bytes = struct.pack(">H", len(json_bytes))
        return b"\xC0\xFF" + length_bytes + json_bytes + b"\xC0"

    @staticmethod
    def unwrap(raw_bytes: bytes) -> Optional[Dict[str, Any]]:
        """Decode a C0 envelope back into its JSON dictionary."""
        try:
            if raw_bytes[0] != 0xC0 or raw_bytes[-1] != 0xC0 or len(raw_bytes) < 4:
                _LOGGER.warning("Invalid Dreame BLE C0 envelope (missing header/footer)")
                return None

            json_text = raw_bytes[3:-1].decode("utf-8", errors="ignore")
            return json.loads(json_text)
        except Exception as ex:
            _LOGGER.error("Failed to decode/unwrap BLE data: %s", ex)
            return None

    # ------------------------------------------------------------------
    # Notification subscription
    # ------------------------------------------------------------------

    async def start_notifications(self) -> bool:
        """Subscribe to mower push notifications on the notification handle."""
        try:
            await self._client.start_notify(
                DREAME_HANDLE_NOTIFICATIONS,
                self._notification_callback,
            )
            _LOGGER.info("BLE notification subscription active")
            return True
        except Exception as ex:
            _LOGGER.error("Failed to start BLE notifications: %s", ex)
            return False

    def _notification_callback(self, handle: int, data: bytearray):
        """Handle incoming notification from the mower."""
        parsed = self.unwrap(bytes(data))
        if parsed is None:
            _LOGGER.debug("Raw notification dropped (unwrap failed): %s", bytes(data).hex())
            return

        q_id = parsed.get("q")
        msg_type = parsed.get("m")
        _LOGGER.debug(
            "Notification received — handle=0x%04x, q=%s, m=%s, payload=%s",
            handle,
            q_id,
            msg_type,
            str(parsed)[:200],
        )

        if q_id is not None and q_id in self._pending:
            # This is a response to one of our pending requests
            fut = self._pending.pop(q_id)
            if not fut.done():
                fut.set_result(parsed)
        else:
            # Unsolicited push notification → fire live update callback
            if self.on_status_update:
                self.on_status_update(parsed)

    # ------------------------------------------------------------------
    # Send + correlate
    # ------------------------------------------------------------------

    async def send_command(
        self, handle: int, payload: dict, wait_for_response: bool = True
    ) -> Optional[Dict[str, Any]]:
        """Send a JSON command to a specific GATT handle and optionally wait for a response."""
        q_id = payload.get("q")

        # Set up future if we want to wait for the mower's reply
        fut: Optional[asyncio.Future] = None
        if wait_for_response and q_id is not None:
            loop = asyncio.get_event_loop_policy().get_event_loop()
            fut = loop.create_future()
            self._pending[q_id] = fut

        try:
            wrapped = self.wrap(payload)
            _LOGGER.debug(
                "Sending to handle 0x%04x (q=%s): %s",
                handle,
                q_id,
                str(payload)[:200],
            )
            await self._client.write_gatt_char(handle, wrapped, response=True)

        except Exception as ex:
            _LOGGER.error("Failed to send BLE command to handle 0x%04x: %s", handle, ex)
            if fut and q_id is not None:
                self._pending.pop(q_id, None)
                if not fut.done():
                    fut.set_exception(ex)
            return None

        # Wait for the mower to reply with matching q-id
        if fut:
            try:
                response = await asyncio.wait_for(fut, timeout=REQUEST_TIMEOUT_SEC)
                _LOGGER.debug("Got matched response for q=%d", q_id)
                return response
            except asyncio.TimeoutError:
                _LOGGER.warning(
                    "Timeout waiting %ds for response to q=%d — mower may not reply to this type of request",
                    REQUEST_TIMEOUT_SEC,
                    q_id,
                )
                self._pending.pop(q_id, None)

        return None

    # ------------------------------------------------------------------
    # High-level convenience methods
    # ------------------------------------------------------------------

    async def read_status(
        self, handle: int, target_type: str, extra_data: Optional[dict] = None
    ) -> Dict[str, Any]:
        """Request specific state from the mower (e.g., Battery, Position)."""
        q_id = self.request_id
        payload = {"m": "g", "t": target_type, "q": q_id}
        if extra_data:
            payload["d"] = extra_data

        _LOGGER.debug("Requesting mower state [%s] with Request ID %s", target_type, q_id)
        return await self.send_command(handle, payload)

    async def start_mowing(self, zone_idx: int = 0) -> bool:
        """Send command to start mowing."""
        q_id = self.request_id
        cmd = {
            "m": "a",
            "p": 0,
            "o": OP_START_MOWING,
            "d": {"idx": zone_idx},
            "q": q_id,
        }
        result = await self.send_command(DREAME_HANDLE_COMMANDS_TASKS, cmd)
        _LOGGER.info("Sent mowing command for zone %d (Q=%d)", zone_idx, q_id)
        return result is not None

    async def dock(self) -> bool:
        """Send command to return to docking station."""
        q_id = self.request_id
        cmd = {"m": "a", "p": 0, "o": OP_DOCK_RETURN, "d": {"idx": 0}, "q": q_id}
        result = await self.send_command(DREAME_HANDLE_COMMANDS_TASKS, cmd)
        _LOGGER.info("Sent dock command (Q=%d)", q_id)
        return result is not None

    async def pause(self) -> bool:
        """Park the mower at its current position."""
        q_id = self.request_id
        cmd = {"m": "a", "p": 0, "o": OP_PARK_AT_POS, "q": q_id}
        result = await self.send_command(DREAME_HANDLE_COMMANDS_TASKS, cmd)
        _LOGGER.info("Sent pause/park command (Q=%d)", q_id)
        return result is not None
