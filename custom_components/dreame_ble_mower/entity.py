"""Entity scaffolding for local BLE mower."""
from __future__ import annotations

import logging
from homeassistant.core import callback
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DreameBleCoordinator

_LOGGER = logging.getLogger(__name__)


class DreameMowerEntity(CoordinatorEntity[DreameBleCoordinator]):
    """A local BLE-connected lawn mower entity."""
    _attr_has_entity_name = True

    def __init__(self, coordinator: DreameBleCoordinator, translation_key) -> None:
        super().__init__(coordinator)
        self._entity_description_key = translation_key
        self._attr_unique_id = f"{coordinator._protocol._client.address}_{translation_key}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, str(self.coordinator._protocol._client.address))},
            identifiers={(DOMAIN, str(self.coordinator._protocol._client.address))},
            name="Dreame Mower",
            manufacturer="Dreame",
            model="Lawn Mower (Local BLE)",
            suggested_area="Garden",
        )
    
    @callback
    def _handle_coordinator_update(self) -> None:
        super()._handle_coordinator_update()
