"""Config flow handler for Dreame BLE Mower integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import async_discovered_devices
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.core import callback
from homeassistant.const import CONF_MAC

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class DreameBleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return DreameOptionsFlowHandler(config_entry)

    async def _async_has_mac(mac_address: str, discovered_dict) -> bool:
        for d in discovered_dict.values():
            # Try case-insensitive match
            if d.address.upper() == mac_address.upper() or \
               (d.name and "dreame" in d.name.lower()):
                return True
        return False

    async def _verify_and_create(self, mac: str) -> ConfigFlowResult:
        """Verify BLE connectivity and create the config entry."""
        try:
            from bleak import BleakClient
            from homeassistant.components.bluetooth import BluetoothServiceInfo
            
            # Find existing device if available
            discovered = []
            for disc in async_discovered_devices(self.hass):
                if disc.address.upper() == mac.upper():
                    discovered.append(disc)
            
            # Use discovered device or fall back to manual connection
            target_device = None
            if discovered:
                target_device = discovered[0]
                _LOGGER.debug("Found existing discovery target for %s", mac)
            
            try:
                async with BleakClient(mac, timeout=15.0) as client:
                    # Try to discover services - this is the real test
                    services = await client.discover_services()
                    service_count = len([s for s in services]) if services else 0
                    
                    _LOGGER.info("Discovered %d services on mower %s", service_count, mac)
                    
                    if service_count > 0:
                        # Success - we can connect and see services
                        return self.async_create_entry(
                            title="Dreame Mower",
                            data=self.entry_data_schema(mac)
                        )
            except Exception as err:
                _LOGGER.debug("Discovery/connection attempt via bleak failed: %s", err)
            
            # Fallback - check if we can at least find the device through BLE scans
            mac_found = False
            for disc in async_discovered_devices(self.hass):
                if hasattr(disc, 'address') and disc.address.upper() == mac.upper():
                    mac_found = True
                    _LOGGER.debug("Found matching MAC %s via discovery", disc.address)
                    break
            
            if mac_found:
                # Device was found in BLE scans - good enough for entry creation
                return self.async_create_entry(
                    title="Dreame Mower",
                    data=self.entry_data_schema(mac)
                )
                
            _LOGGER.warning("Could not verify connection to %s", mac)
        
        except Exception as err:
            _LOGGER.exception("Unexpected error during BLE verification: %s", err)
        
        return None  # Caller handles error UI

    def entry_data_schema(self, mac: str):
        """Return standard data schema for config entry."""
        return {CONF_MAC: mac}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the user-initiated setup."""
        errors: dict[str, str] = {}
        
        if user_input is not None:
            self.hass.data.setdefault(DOMAIN, {})
            
            # Reuse the shared verify-and-create logic
            mac: str = user_input.get(CONF_MAC) or ""
            result = await self._verify_and_create(mac)
            if result is not None:
                return result
                
            errors = {"base": "cannot_connect"}

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_MAC): str}),
            errors=errors,
            description_placeholders={}
        )


class DreameOptionsFlowHandler(ConfigFlow):
    """Handle options flow."""
    pass
