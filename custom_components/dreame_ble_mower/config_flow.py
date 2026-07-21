"""Config flow for Dreame Mower BLE integration."""

from collections.abc import Mapping
import logging
from typing import Any, override

from bleak import BleakError, BleakClient
import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfo
from homeassistant.config_entries import SOURCE_BLUETOOTH, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS, CONF_PIN

from .const import DOMAIN, LOGGER

BLUETOOTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ADDRESS): str,
    }
)

REAUTH_SCHEMA = BLUETOOTH_SCHEMA


class DreameBleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the Dreame Mower BLE integration."""

    VERSION = 1

    address: str | None = None
    mower_name: str = ""

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
        self.address = discovery_info.address

        await self.async_set_unique_id(self.address)
        self._abort_if_unique_id_configured()

        return await self.async_step_bluetooth_confirm()

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial manual step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self.address = user_input[CONF_ADDRESS].strip().upper()
            await self.async_set_unique_id(self.address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return await self.check_mower(user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                BLUETOOTH_SCHEMA, user_input
            ),
            errors=errors,
        )

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm discovery for the detected mower."""
        assert self.address
        errors: dict[str, str] = {}

        if user_input is not None:
            self.address = user_input[CONF_ADDRESS].strip().upper()
            return await self.check_mower(user_input)

        return self.async_show_form(
            step_id="bluetooth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                BLUETOOTH_SCHEMA, user_input
            ),
            description_placeholders={"name": self.mower_name},
            errors=errors,
        )

    async def check_mower(self, _user_input: dict[str, Any]) -> ConfigFlowResult | None:
        """Check if we can connect to the mower."""
        LOGGER.debug("Checking connection to %s ...", self.address)

        device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )

        if not device:
            LOGGER.error("Mower at address %s not found nearby", self.address)
            return None

        try:
            from bleak_retry_connector import establish_connection

            client = await establish_connection(
                BleakClient,
                device.address,
                f"{self.mower_name or 'Dreame Mower'}",
            )

            service_count = len(client.services) if client.services else 0
            LOGGER.info("Mower %s has %d GATT services", self.address, service_count)

            await client.disconnect()

            if service_count > 0:
                return self.async_create_entry(
                    title=self.mower_name or f"Dreame Mower {self.address[:8]}",
                    data={CONF_ADDRESS: self.address},
                )

        except (TimeoutError, BleakError):
            LOGGER.warning("Failed to connect to mower", exc_info=True)
        except Exception:
            LOGGER.exception("Unexpected error during connection check")

        # If we failed here and there's already an entry, try re-auth path instead
        if self.context.get("source") == SOURCE_BLUETOOTH:
            return self.async_abort(reason="cannot_connect")

        errors = {"base": "cannot_connect"}
        user_input_to_revert = _user_input or {}
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                BLUETOOTH_SCHEMA, user_input_to_revert
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Perform reauthentication upon an API authentication error."""
        reauth_entry = self._get_reauth_entry()

        reauth_address = entry_data.get(CONF_ADDRESS) or (self.address or "")

        self.address = reauth_address
        self.mower_name = reauth_entry.title
        self.context["title_placeholders"] = {
            "name": self.mower_name,
            "address": reauth_address,
        }
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauthentication dialog."""
        errors: dict[str, str] = {}

        if user_input is not None:
            reauth_entry = self._get_reauth_entry()

            new_data = {CONF_ADDRESS: self.address}
            if reauth_entry.data.get(CONF_PIN):
                new_data[CONF_PIN] = reauth_entry.data[CONF_PIN]

            try:
                device = bluetooth.async_ble_device_from_address(
                    self.hass, self.address, connectable=True
                )

                if device:
                    from bleak_retry_connector import establish_connection

                    client = await establish_connection(
                        BleakClient,
                        device.address,
                        f"{self.mower_name}",
                    )
                    await client.disconnect()
                    return self.async_update_reload_and_abort(
                        reauth_entry, data=new_data
                    )

            except (TimeoutError, BleakError):
                errors["base"] = "cannot_connect"
            except Exception:
                LOGGER.exception("Unexpected error during re-auth check")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                REAUTH_SCHEMA, user_input
            ),
            description_placeholders={"name": self.mower_name},
            errors=errors,
        )
