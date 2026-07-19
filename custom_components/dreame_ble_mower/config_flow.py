"""Config flow handler."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import async_discovered_devices
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_MAC
from homeassistant.core import callback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def _async_has_mac(mac_address: str, discovered_dict) -> bool:
    for d in discovered_dict.values():
        if d.address == mac_address.upper() or d.name and "dreame" in d.name.lower():
            return True
    return False


class DreameBleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return DreameOptionsFlowHandler(config_entry)

    async def async_step_bluetooth(self, user_input) -> ConfigFlowResult:
        auto_match = []
        discovered_dreame = {}

        for disc in async_discovered_devices(self.hass):
            if (disc.name and "dreame" in disc.name.lower()):
                auto_match.append(disc.address)
                discovered_dreame[disc.name] = disc.address

        data_schema = vol.Schema({})
        if not auto_match:
            # No Dreame mowers discovered in Bluetooth LE yet. Ask user for MAC.
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({vol.Required(CONF_MAC): str}),
                errors={},
                description_placeholders={}
            )

        return self.async_show_form(
            step_id="bluetooth",
            data_schema=vol.Schema({vol.Optional(CONF_MAC, default=auto_match[0]): vol.In(discovered_dreame)}),
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            mac = user_input.get(CONF_MAC)
            await self.async_set_unique_id(mac)
            self._abort_if_unique_id_configured({CONF_MAC: mac})

            # Verify connectivity to the mower before adding the entry
            from bleak import BleakClient
            try:
                async with BleakClient(mac) as client:
                    if await client.is_connected and len(client.services) > 0:
                        return self.async_create_entry(title="Dreame Mower", data={CONF_MAC: mac})
            except Exception:
                return self.async_show_form(
                    step_id="user",
                    errors={"base": "cannot_connect"},
                    data_schema=vol.Schema({vol.Required(CONF_MAC): str}),
                )

        return self.async_show_form(step_id="user", data_schema=vol.Schema({vol.Required(CONF_MAC): str}))


class DreameOptionsFlowHandler(ConfigFlow):
    """Handle options flow."""
    ...
