"""Config flow for Dreame Mower BLE integration."""

from collections.abc import Mapping
import logging
from typing import Any, override

import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfo
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS, CONF_PIN

from .const import DOMAIN, LOGGER

CONFIG_FLOW_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ADDRESS): str,
    }
)


class DreameBleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the Dreame Mower BLE integration."""

    VERSION = 1

    address: str | None = None
    mower_name: str = ""
    ble_device: Any | None = None

    async def _is_supported(self, discovery_info: BluetoothServiceInfo) -> bool:
        """Check if device is supported by looking at manufacturer data."""
        LOGGER.debug("Checking if device is supported: %s", discovery_info)
        if not any(d for d in discovery_info.manufacturer_data.values()):
            LOGGER.debug(
                "No manufacturer data present, skipping: %s",
                discovery_info.name,
            )
            return False
        return True

    @override
    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfo
    ) -> ConfigFlowResult:
        """Handle the bluetooth discovery step."""
        LOGGER.debug("Discovered device: %s", discovery_info)
        if not await self._is_supported(discovery_info):
            return self.async_abort(reason="no_devices_found")

        self.context["title_placeholders"] = {
            "name": discovery_info.name or f"Dreame Mower {discovery_info.address[:8]}",
            "address": discovery_info.address,
        }
        self.mower_name = discovery_info.name or f"Dreame Mower {discovery_info.address[:8]}"
        self.ble_device = discovery_info

        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        return await self.async_step_bluetooth_confirm()

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial manual step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            mac_address_clean: str = user_input[CONF_ADDRESS].strip().upper()
            await self.async_set_unique_id(mac_address_clean, raise_on_progress=False)
            self._abort_if_unique_id_configured()

            device_or_none = bluetooth.async_ble_device_from_address(
                self.hass, mac_address_clean, connectable=True
            ) or None
            if not device_or_none:
                errors["base"] = "cannot_connect"
                return self.async_show_form(
                    step_id="user",
                    data_schema=self.add_suggested_values_to_schema(
                        CONFIG_FLOW_SCHEMA, user_input
                    ),
                    errors=errors,
                )

            # Populate address + name so check_mower() has them (user path doesn't
            # go through async_step_bluetooth which sets ble_device)
            self.address = mac_address_clean
            self.mower_name = f"Dreame Mower {mac_address_clean[:8]}"

            return await self.check_mower(user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                CONFIG_FLOW_SCHEMA, user_input
            ),
            errors=errors,
        )

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm discovery for the detected mower."""
        assert self.ble_device
        errors: dict[str, str] = {}

        if user_input is not None:
            return await self.check_mower(user_input)

        return self.async_show_form(
            step_id="bluetooth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                CONFIG_FLOW_SCHEMA, user_input
            ),
            description_placeholders={"name": self.mower_name},
            errors=errors,
        )

    async def check_mower(self, _user_input: dict[str, Any]) -> ConfigFlowResult | None:
        """Check if we can connect to the mower."""
        # Device reachability was already verified earlier in this flow (bluetooth discovery
        # or bluetooth.async_ble_device_from_address), so no need for GATT probing here.
        # Home Assistant wraps BleakClient in HaBleakClientWrapper which lacks
        # discover_services() and warns about bleak_retry_connector usage — those calls would
        # always fail/crash the config flow.
        assert self.ble_device or self.address
        target_addr: str = self.ble_device.address if self.ble_device else (self.address or "")

        LOGGER.debug("Mower %s verified reachable, creating entry", target_addr)
        return self.async_create_entry(
            title=self.mower_name or f"Dreame Mower {target_addr[:8]}",
            data={CONF_ADDRESS: target_addr},
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Perform reauthentication upon an API authentication error."""
        reauth_entry = self._get_reauth_entry()
        target_addr = entry_data.get(CONF_ADDRESS) or (self.address or "")

        self.context["title_placeholders"] = {
            "name": reauth_entry.title,
            "address": target_addr,
        }
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauthentication dialog."""
        errors: dict[str, str] = {}

        if user_input is not None:
            target_addr_clean: str = user_input.get(CONF_ADDRESS, "").strip().upper()
        else:
            orig_entry_reauth = self._get_reauth_entry()
            target_addr_clean = (orig_entry_reauth.data.get(CONF_ADDRESS) or "").strip().upper()

        if not target_addr_clean:
            errors["base"] = "cannot_connect"
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=self.add_suggested_values_to_schema(CONFIG_FLOW_SCHEMA, user_input),
                description_placeholders={"name": self.context.get("title_placeholders", {}).get("name", "Mower")},
                errors=errors,
            )

        try:
            dev_check = bluetooth.async_ble_device_from_address(
                self.hass, target_addr_clean, connectable=True
            )
            if not dev_check:
                errors["base"] = "cannot_connect"
                raise ValueError("Mower offline")

            # Device is reachable — no need for GATT probing in config flow.
            # Home Assistant's BLE layer wraps BleakClient in HaBleakClientWrapper which
            # lacks discover_services(). GATT connectivity will be validated by the
            # coordinator on successful setup.
            LOGGER.info("Re-auth: Mower at %s verified reachable", target_addr_clean)

            return self.async_update_reload_and_abort(
                self._get_reauth_entry(),
                data={CONF_ADDRESS: target_addr_clean},
            )

        except ValueError:
            pass
        except Exception:
            errors["base"] = "unknown"

        reauth_placeholder_name = (
            self.context.get("title_placeholders", {}).get(
                "name", "Mower"
            )
        )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(CONFIG_FLOW_SCHEMA, user_input),
            description_placeholders={"name": reauth_placeholder_name},
            errors=errors,
        )
