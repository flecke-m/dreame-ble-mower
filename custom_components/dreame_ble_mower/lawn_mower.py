"""Custom component to support Dreame Mower via BLE."""

import logging
from homeassistant.components.lawn_mower import LawnMowerActivity, LawnMowerEntity
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
    """Set up the lawn mower platform."""
    coordinator: DreameBleCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities([DreameMower(coordinator)])


class DreameMower(DreameMowerEntity, LawnMowerEntity):
    """Representation of a local BLE-connected Dreame Mower."""

    def __init__(self, coordinator: DreameBleCoordinator) -> None:
        super().__init__(coordinator, "lawn_mower")
        self._attr_name = "Dreame Mower"

    @property
    def activity(self) -> LawnMowerActivity:
        return {
            "mowing": LawnMowerActivity.MOWING,
            "paused": LawnMowerActivity.PAUSED,
            "error": LawnMowerActivity.ERROR,
            "returning_to_station_to_charge": LawnMowerActivity.RETURNING,
            "docked": LawnMowerActivity.DOCKED,
        }.get(self._attr_activity, LawnMowerActivity.DOCKED)

    async def async_start_mowing(self) -> None:
        await self.coordinator._protocol.start_mowing(zone_idx=0)
        await self.coordinator.async_request_refresh()

    async def async_dock(self) -> None:
        await self.coordinator._protocol.dock()
        await self.coordinator.async_request_refresh()

    async def async_pause(self) -> None:
        await self.coordinator._protocol.pause()
        await self.coordinator.async_request_refresh()
