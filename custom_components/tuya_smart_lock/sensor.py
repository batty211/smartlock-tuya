"""Sensor entities for Tuya Smart Lock."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DEVICE_ID, CONF_DEVICE_NAME, DOMAIN

BATTERY_PERCENT_ESTIMATES = {
    "high": 75,
    "medium": 50,
    "low": 20,
    "poweroff": 0,
}

BATTERY_ICONS = {
    "high": "mdi:battery",
    "medium": "mdi:battery",
    "low": "mdi:battery-alert",
    "poweroff": "mdi:battery-outline",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities from config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    api = data["api"]
    entry_data = data["entry_data"]
    device_id = entry_data[CONF_DEVICE_ID]
    device_name = entry_data[CONF_DEVICE_NAME]

    async_add_entities([TuyaSmartLockBatterySensor(api, device_id, device_name)], True)


class TuyaSmartLockBatterySensor(SensorEntity):
    """Battery state sensor for a Tuya smart lock."""

    _attr_has_entity_name = True
    _attr_name = "Battery"

    def __init__(self, api, device_id: str, device_name: str) -> None:
        self._api = api
        self._device_id = device_id
        self._device_name = device_name
        self._battery_state: str | None = None
        self._attr_unique_id = f"tuya_smart_lock_{device_id}_battery"

    @property
    def device_info(self):
        """Link to the existing Tuya device if present, otherwise create our own."""
        return {
            "identifiers": {("tuya", self._device_id)},
            "name": self._device_name,
            "manufacturer": "Tuya",
        }

    @property
    def native_value(self) -> str | None:
        """Return the raw Tuya battery state."""
        return self._battery_state

    @property
    def extra_state_attributes(self) -> dict:
        """Return estimated battery percentage as an attribute."""
        return {
            "battery_percent_estimate": BATTERY_PERCENT_ESTIMATES.get(
                self._battery_state
            )
        }

    @property
    def icon(self) -> str:
        """Return an icon based on battery state."""
        return BATTERY_ICONS.get(self._battery_state, "mdi:battery-unknown")

    async def async_update(self) -> None:
        """Fetch the latest battery state from Tuya Cloud."""
        self._battery_state = await self._api.async_get_battery_state(self._device_id)
