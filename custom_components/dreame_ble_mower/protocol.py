#!/usr/bin/env python3
"""Dreame Mower Local BLE Protocol Bridge.

Protocol ground truth (byte-verified from newBLElog.pcap frames 673/803/
804/810/816/836 and dreame-wireshark.pcap; decompiled app cross-checked):
- The mower advertises ONE 128-bit data service:
    743345ba-72ea-4343-bd74-4b4c16040000
  (GATT handles 0x0014..0x003a per Read By Group Type Response, f673.)
- Zero SMP / LTK / bonding frames in ANY capture. No pairing, no PIN —
  the phone connects and immediately does unencrypted GATT R/W.
- Handles confirmed on the wire:
    0x0020  command write (write-with-response, "m":"g"/"m":"a" frames)
    0x0021  its CCCD — subscribed, notifications delivered on 0x001d
    0x0023  auxiliary write (app sends {"t":"TIME"} here first)
    0x001d  data read / notification target ({"m":"r","r":-3} etc.)
- App request sequence (f803→836):
    1. write 0x0023  (e.g. time sync)
    2. write 0x0020  {"m":"g","t":"CFG","q":1}
    3. read  0x001d  (transient, r may be -3)
    4. write CCCD 0x0021 = enable notify (01 00)
    5. read  0x0020  → full response JSON
  Reads are the primary response path; notifications are auxiliary.

- Envelope (byte-verified, both directions):
      C0 00 <len:u8 = byte-length of body> <body…> C0
  Examples from the wire:
    write f804 : c0 00 19 7b 22 6d 22 3a 22 67 22 … 7d c0   (len 0x19=25)
    resp  f816 : c0 00 10 7b 22 6d 22 3a 22 72 22 3a 22 … 7d c0 (len 0x10=16)

Response code field ("r"):
  0   = OK
  -3  = error (mower-side fault; e.g. zone not ready, auth, in-transit)
"""
from __future__ import annotations

import asyncio
import json
import logging
import struct
from typing import Any, Callable, Dict, List, Optional

from bleak import BleakClient, BleakError

from .const import MOWER_SERVICE_UUID

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Op codes observed in captures (mower→app command vocabulary).
# Not every op is documented; we use what we saw in successful app sessions.
# ---------------------------------------------------------------------------
OP_START_MOWING   = 207   # "a", "p":0, "o":207, "d":{"idx":zone}
OP_DOCK           = 200   # "a", "p":0, "o":200, "d":{"idx":0}
OP_PARK           = 202   # "a", "p":0, "o":202
OP_GET_STATUS     = -1    # use GET on "CFG" / "TASK" targets instead
REQUEST_TIMEOUT_SEC = 5.0
HEARTBEAT_HANDLE  = 0x0017  # 1 Hz notify channel, ignore its payload


# ---------------------------------------------------------------------------
# Envelope helpers  (C0 00 00 <len16LE-of-body-included> <json> C0)
# ---------------------------------------------------------------------------

def wrap_envelope(payload: dict | bytes) -> bytes:
    """Encode into the mower envelope.

    Layout (byte-verified against wire frames f803/f804/f810 in
    newBLElog.pcap, both request and response direction):
        C0 00 <len:u8> <body bytes> C0
    where len is the *exact* byte count of the body (does NOT include
    the header or the trailing C0). Verified examples:
        body = 25 B JSON  →  c0 00 19 <25 B json> c0   (f804)
        body = 40 B JSON  →  c0 00 28 <40 B json> c0   (f810)
        body = 16 B JSON  →  c0 00 10 <16 B json> c0   (f816)
    """
    if isinstance(payload, (dict, list)):
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    else:
        body = bytes(payload)
    if len(body) > 254:
        raise ValueError(
            f"Dreame envelope body is {len(body)} bytes — max is 254 "
            "(1-byte length field)"
        )
    return b"\xC0\x00" + bytes([len(body)]) + body + b"\xC0"


def unwrap_envelope(raw: bytes) -> Optional[Dict[str, Any]]:
    """Decode the mower envelope `C0 00 <len:u8> <body> C0`.

    Returns the parsed JSON body, or None if the payload is not a
    valid JSON envelope (e.g. the 1-byte 0x00 acks seen on notify).
    """
    if not raw:
        return None
    # Envelope: C0 00 LEN BODY C0
    if (
        len(raw) >= 6
        and raw[0] == 0xC0
        and raw[1] == 0x00
        and raw[-1] == 0xC0
    ):
        declared = raw[2]
        if declared == len(raw) - 4:
            body = raw[3:-1]
            try:
                parsed = json.loads(body.decode("utf-8"))
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                _LOGGER.debug(
                    "Envelope length matched but body is not JSON: %s — raw=%s",
                    e, raw.hex(),
                )
                return None
    _LOGGER.debug(
        "Non-envelope notification payload: %s", raw.hex()[:96],
    )
    return None


# ---------------------------------------------------------------------------
# Protocol class
# ---------------------------------------------------------------------------

class DreameBLEProtocol:
    """High-level Dreame mower protocol over bleak.

    Usage:
        protocol = DreameBLEProtocol(client)
        await protocol.discover_characteristics()
        await protocol.start_notifications()

        # GET config (battery, TZ, ...):
        cfg = await protocol.read_config()

        # Actions:
        ok = await protocol.start_mowing(zone=1)
        ok = await protocol.dock()
        ok = await protocol.park()
    """

    def __init__(self, client: BleakClient):
        self._client = client
        self._q_counter = 0  # app starts its q-counter at 1 after +1

        # Pending futures keyed by (q, expect_read_handle)
        self._pending: Dict[object, asyncio.Future] = {}

        # Discovered handles — populated by discover_characteristics()
        self._write_handles: List[int] = []    # every writable char handle
        self._read_handles: List[int] = []     # every readable char handle
        self._notify_handles: List[int] = []   # every notify-capable handle
        # ATT handle → characteristic object. Bleak client methods expect the
        # characteristic object (a UUID string), NOT the ATT handle — passing
        # "0x00NN" makes bleak parse it as a UUID string and raise
        # "badly formed hexadecimal UUID string".
        self._char_by_handle: Dict[int, Any] = {}

        # Backwards-compat properties (used by coordinator.py currently)
        self.handle_notifications: Optional[int] = None
        self.handle_commands_tasks: Optional[int] = None
        self.handle_device_status: Optional[int] = None
        self.handle_mpos_positioning: Optional[int] = None

        self.on_status_update: Optional[Callable[[dict], None]] = None

    # ------------------------------------------------------------------
    # Request ID
    # ------------------------------------------------------------------
    def next_q(self) -> int:
        self._q_counter += 1
        return self._q_counter

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    async def discover_characteristics(self) -> None:
        """Find all writable/readable/notify characteristics in the mower's
        custom GATT service. We treat every writable char as a valid command
        target and every readable char as a valid data source (matches what
        the official app does on every frame we've captured)."""
        services = self._client.services
        # Strategy: prefer the largest non-BASE service that is NOT the
        # 16-bit standard services (GAP 0x1800 / GATT 0x1801 / Dev Info 0x180A)
        # and has at least one characteristic that is BOTH writable and has
        # multiple characteristics (mower service shape).
        # Anchor discovery to the verified mower service UUID first (from pcap
        # frame 673: 743345ba-72ea-4343-bd74-4b4c16040000). Fall back to the
        # largest non-base GATT service if that UUID isn't present (BLE stack
        # may normalise it).
        uuid_target = MOWER_SERVICE_UUID.replace("-", "").lower()
        target_service = None
        for svc in services:
            if str(svc.uuid).replace("-", "").lower() == uuid_target:
                target_service = svc
                break
        if target_service is None:
            base16s = {"1800", "1801", "180a", "180c", "180f", "1812"}
            best_chars = 0
            for svc in services:
                short = str(svc.uuid).split("-")[0].lower()
                if short in base16s:
                    continue
                if len(svc.characteristics) > best_chars:
                    best_chars = len(svc.characteristics)
                    target_service = svc
        if target_service is not None:
            log_min = min((c.handle for c in target_service.characteristics), default=0)
            log_max = max((c.handle for c in target_service.characteristics), default=0)
            _LOGGER.info(
                "Dreame GATT service: %s  (handles %s..%s, %d chars)",
                target_service.uuid, hex(log_min), hex(log_max),
                len(target_service.characteristics),
            )

        for char in (target_service.characteristics if target_service else []):
            props = set(char.properties or ())
            is_writable = any(p in props for p in ("write", "write-with-response"))
            is_readable = "read" in props
            is_notify = any(p in props for p in ("notify", "indicate"))
            _LOGGER.debug(
                "  char 0x%04x  props=%s  writable=%s  readable=%s  notify=%s",
                char.handle, sorted(props), is_writable, is_readable, is_notify,
            )
            self._char_by_handle[char.handle] = char
            if is_writable:
                self._write_handles.append(char.handle)
            if is_readable:
                self._read_handles.append(char.handle)
            if is_notify:
                self._notify_handles.append(char.handle)

        # Backwards-compat property slots — coordinator.py still reads these
        if self._write_handles:
            self.handle_commands_tasks = self._write_handles[0]
            self.handle_device_status = self._write_handles[0]
            self.handle_mpos_positioning = self._write_handles[0]
        if self._notify_handles:
            self.handle_notifications = self._notify_handles[0]

        _LOGGER.info(
            "Resolved handles — write=%s  read=%s  notify=%s",
            [hex(h) for h in self._write_handles],
            [hex(h) for h in self._read_handles],
            [hex(h) for h in self._notify_handles],
        )

    # ------------------------------------------------------------------
    # Notify subscription
    # ------------------------------------------------------------------
    async def start_notifications(self) -> bool:
        """Subscribe to every notify-capable characteristic, mirroring the
        phone app (which subscribes to ALL 8 characteristics)."""
        ok = False
        for h in self._notify_handles:
            char = self._char_by_handle.get(h)
            if char is None:
                continue
            try:
                await self._client.start_notify(char, self._on_notify)
                _LOGGER.debug("Subscribed to notifications on 0x%04x", h)
                ok = True
            except Exception as ex:
                # Some characteristics' CCCD write may fail with
                # "Attribute is not valid for notify" on older firmware —
                # that's fine; the phone app's success only needs the
                # actual data characteristic to be subscribed.
                _LOGGER.debug("Notify sub 0x%04x failed: %s", h, ex)
        _LOGGER.info("Notify subscription attempted on %d handles", len(self._notify_handles))
        return ok

    def _on_notify(self, _char: str, data: bytearray) -> None:
        """Incoming notification from the mower."""
        parsed = unwrap_envelope(bytes(data))
        if parsed is None:
            return

        q = parsed.get("q")
        msg = parsed.get("m")
        _LOGGER.debug("NOTIFY q=%s m=%s %s", q, msg, json.dumps(parsed)[:220])

        if q is not None and (q in self._pending):
            fut = self._pending.get(q)
            if fut and not fut.done():
                fut.set_result(parsed)
        elif self.on_status_update is not None:
            self.on_status_update(parsed)

    # ------------------------------------------------------------------
    # Core send + read
    # ------------------------------------------------------------------
    async def _send_one(self, handle: int, payload: dict) -> None:
        char = self._char_by_handle.get(handle)
        if char is None:
            raise BleakError(f"No characteristic object for handle 0x{handle:04x}")
        envelope = wrap_envelope(payload)
        _LOGGER.debug(
            "→ 0x%04x  q=%s  %s  (env=%s)",
            handle, payload.get("q"), json.dumps(payload)[:200],
            envelope.hex()[:60] + "…",
        )
        await self._client.write_gatt_char(char, envelope, response=True)

    async def _read_one(self, handle: int) -> Optional[dict]:
        char = self._char_by_handle.get(handle)
        if char is None:
            raise BleakError(f"No characteristic object for handle 0x{handle:04x}")
        raw = await self._client.read_gatt_char(char)
        parsed = unwrap_envelope(bytes(raw))
        if parsed is not None:
            _LOGGER.debug("← 0x%04x  q=%s  %s", handle, parsed.get("q"), json.dumps(parsed)[:200])
        return parsed

    async def send_request(
        self,
        payload: dict,
        prefer_write: Optional[int] = None,
        timeout: float = REQUEST_TIMEOUT_SEC,
    ) -> dict:
        """Send a command to the mower and wait for its response.

        The app flow (per captures):
          1. Write envelope(json with q=N) to a writable handle.
          2. Read from a readable handle to pull the response with q=N.

        We try each writable handle in order; if a write fails we move on.
        Then we try each readable handle in order for a response whose q
        matches. We also check for an in-flight notify that already
        delivered the response.

        Return: parsed response dict; raises on timeout / all-failed.
        """
        q = payload.get("q")
        loop = asyncio.get_event_loop()
        fut: Optional[asyncio.Future] = None
        if q is not None:
            fut = loop.create_future()
            self._pending[q] = fut

        try:
            # Phase 1: send
            candidates: List[int] = (
                [prefer_write, *self._write_handles] if prefer_write is not None
                else list(self._write_handles)
            )
            if not candidates:
                raise BleakError("No writable characteristic discovered")
            sent = False
            last_err: Optional[BaseException] = None
            for h in dict.fromkeys(candidates):  # dedupe, keep order
                if fut is not None and fut.done():
                    break
                try:
                    await self._send_one(h, payload)
                    sent = True
                    break
                except BleakError as ex:
                    last_err = ex
                    _LOGGER.warning(
                        "Write to 0x%04x failed: %s — trying next handle",
                        h, ex,
                    )
            if not sent and fut is not None and fut.done():
                pass  # notify raced ahead
            elif not sent:
                if fut is not None:
                    self._pending.pop(q, None)
                if last_err is not None:
                    raise last_err
                raise BleakError("No writable handle accepted the command")

            # Phase 2: try notify-race first (some responses come in-flight)
            if fut is not None:
                try:
                    return await asyncio.wait_for(asyncio.shield(fut), timeout=0.5)
                except (asyncio.TimeoutError, asyncio.InvalidStateError):
                    pass
                except Exception:
                    raise

            # Phase 3: poll reads (mower typically responds on the READABLE
            # handle with a payload matching our q)
            any_result: Optional[dict] = None
            q_match: Optional[dict] = None
            last_err = None
            for h in self._read_handles:
                try:
                    parsed = await self._read_one(h)
                except BleakError as ex:
                    last_err = ex
                    continue
                except Exception as ex:
                    last_err = ex
                    continue
                if parsed is None:
                    continue
                if q is not None:
                    if parsed.get("q") == q:
                        return parsed
                else:
                    return parsed
                any_result = parsed
            if fut is not None:
                try:
                    return await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
                except (asyncio.TimeoutError, asyncio.InvalidStateError):
                    pass
            if q_match is not None:
                return q_match
            if any_result is not None:
                return any_result
            if last_err is not None:
                raise last_err
            _LOGGER.debug("no readable handle replied for q=%s", q)
            return {}

        finally:
            if fut is not None and not fut.done():
                self._pending.pop(q, None)
                if not fut.cancelled():
                    fut.cancel()

    # ------------------------------------------------------------------
    # High-level API
    # ------------------------------------------------------------------
    async def read_status(self, target: str) -> dict:
        """Generic GET request: m=g, t=<target> ('CFG', 'TASK', 'PREI'…).

        Writes the request to the preferred command handle, then polls
        readable handles for the response with the matching q.
        Returns the full parsed dict, or {} on timeout.
        """
        q = self.next_q()
        payload = {"m": "g", "t": target, "q": q}
        return await self.send_request(
            payload, prefer_write=self.handle_commands_tasks
        )

    async def read_config(self) -> dict:
        """GET CFG — battery, TZ, VER, LIT etc.  (q=1 in captures)"""
        return await self.read_status("CFG")

    async def read_prei(self, zone_idx: int = 0) -> dict:
        """GET PREI — pre-mowing status for a zone."""
        q = self.next_q()
        payload = {
            "m": "g", "t": "PREI",
            "d": {"idx": zone_idx},
            "q": q,
        }
        return await self.send_request(
            payload, prefer_write=self.handle_commands_tasks
        )

    async def ping(self) -> int:
        """Heartbeat the phone app's `o:207` keepalive? Actually the app
        writes `{"m":"g","t":"CFG","q":1}` first. Let's just use a read_config."""
        r = await self.read_config()
        return int(r.get("r", -1))

    async def start_mowing(self, zone_idx: int = 0) -> bool:
        """Send 'start mowing in zone N' command."""
        q = self.next_q()
        payload = {
            "m": "a", "p": 0, "o": OP_START_MOWING,
            "d": {"idx": zone_idx}, "q": q,
        }
        _LOGGER.info("Start mowing (zone=%d, q=%d)", zone_idx, q)
        try:
            r = await self.send_request(payload)
        except Exception as ex:
            _LOGGER.error("start_mowing failed: %s", ex)
            return False
        code = int(r.get("r", -1)) if r else -1
        _LOGGER.info("start_mowing response: r=%d  full=%s", code, json.dumps(r)[:200])
        return code == 0

    async def dock(self) -> bool:
        q = self.next_q()
        payload = {"m": "a", "p": 0, "o": OP_DOCK, "d": {"idx": 0}, "q": q}
        _LOGGER.info("Dock (q=%d)", q)
        try:
            r = await self.send_request(payload)
        except Exception as ex:
            _LOGGER.error("dock failed: %s", ex)
            return False
        code = int(r.get("r", -1)) if r else -1
        _LOGGER.info("dock response: r=%d", code)
        return code == 0

    async def pause(self) -> bool:
        """Pause mowing (op 202 — the 'paused' state in the wire state machine)."""
        return await self.park()

    async def park(self) -> bool:
        q = self.next_q()
        payload = {"m": "a", "p": 0, "o": OP_PARK, "q": q}
        _LOGGER.info("Park (q=%d)", q)
        try:
            r = await self.send_request(payload)
        except Exception as ex:
            _LOGGER.error("park failed: %s", ex)
            return False
        code = int(r.get("r", -1)) if r else -1
        _LOGGER.info("park response: r=%d", code)
        return code == 0
