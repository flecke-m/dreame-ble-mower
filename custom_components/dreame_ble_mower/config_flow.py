"""Config flow for Dreame Mower BLE integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_MAC
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN
from .coordinator import DreameBleCoordinator
from .protocol import DreameBLEProtocol

_LOGGER = logging.getLogger(__name__)

DEVICE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_MAC): cv.string,
    }
)


class DreameBleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the Dreame Mower BLE integration."""

    VERSION = 1

    address: str | None = None
    name: str = ""
    pin: str | None = None
    device_id: str = ""

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle the bluetooth discovery step."""
        _LOGGER.debug("Discovered Dreame device via BLE advertisement: %s", discovery_info)

        # Abort early if already configured
        await self.async_set_unique_id((discovery_info.address or "").upper())
        self._abort_if_unique_id_configured()

        self.context["title_placeholders"] = {
            "name": discovery_info.name or "Dreame Mower",
            "address": (discovery_info.address or "").upper(),
        }
        self.address = (discovery_info.address or "").upper()

        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the confirmation step for a discovered Bluetooth device."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Proceed to probe the mower before creating entry
            result = await self._probe_and_create_entry()
            if result is not None:
                return result
            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="bluetooth_confirm",
            data_schema=DEVICE_SCHEMA,
            description_placeholders={
                "name": self.name or self.address.upper(),
            },
            errors=errors,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the manual setup (MAC address + optional PIN) entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            mac_str: str = (user_input.get(CONF_MAC) or "").strip().upper()
            self.address = mac_str
            await self.async_set_unique_id(mac_str, raise_on_progress=False)
            self._abort_if_unique_id_configured()

            result = await self._probe_and_create_entry()
            if result is not None:
                return result

            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(DEVICE_SCHEMA, user_input),
            errors=errors,
        )

    async def _probe_and_create_entry(self) -> ConfigFlowResult | None:
        """Probe the mower via BLE and create config entry if successful."""
        assert self.address is not None

        device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if device is None:
            _LOGGER.warning("Device %s not found among known/discovered devices", self.address)
            return None

        try:
            from bleak import BleakClient

            # Try connecting & discovering services (same as Husqvarna pattern)
            async with BleakClient(device.address, timeout=15.0) as client:
                await client.discover_services()
                service_count = len(client.services) if client.services else 0
                _LOGGER.info("Device %s has %d GATT services", self.address, service_count)

                # At least some services expected for a mower device
                if service_count > 0:
                    title = f"Dreame Mower {self.address}"
                    return self.async_create_entry(
                        title=title,
                        data={CONF_MAC: self.address},
                    )

            _LOGGER.warning("Device had zero discoverable services — skipping entry creation.")
        except Exception as err:
            _LOGGER.debug("BLE connection probe failed to %s: %s", self.address, err)

        return None
