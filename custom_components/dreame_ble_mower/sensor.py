"""Battery sensor platform for local BLE mower."""
from __future__ import annotations

import logging
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import DreameBleCoordinator
from .entity import DreameMowerEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    coordinator: DreameBleCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities([DreameBatterySensor(coordinator)])


class DreameBatterySensor(DreameMowerEntity, SensorEntity):
    """Battery level of the local mower."""

    def __init__(self, coordinator: DreameBleCoordinator) -> None:
        super().__init__(coordinator, "battery")
        self._attr_name = "Battery"
        self._attr_native_unit_of_measurement = "%"
        self._attr_device_class = SensorDeviceClass.BATTERY

    @property
    def native_value(self) -> int | float | None:
        return self.coordinator.data.get("battery_percent")
