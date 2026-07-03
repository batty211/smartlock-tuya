"""Binary sensor entities for Smart (Con)lock tuya."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DEVICE_CATEGORY, CONF_DEVICE_ID, CONF_DEVICE_NAME, DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensor entities from config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    entry_data = data["entry_data"]

    if entry_data.get(CONF_DEVICE_CATEGORY) != "jtmspro":
        return

    api = data["api"]
    device_id = entry_data[CONF_DEVICE_ID]
    device_name = entry_data[CONF_DEVICE_NAME]

    async_add_entities(
        [
            TuyaSmartLockOnlineSensor(api, device_id, device_name),
            TuyaSmartLockCallActiveSensor(api, device_id, device_name),
        ],
        True,
    )


class TuyaSmartLockBinarySensor(BinarySensorEntity):
    """Base binary sensor for a Tuya smart lock."""

    _attr_has_entity_name = True

    def __init__(self, api, device_id: str, device_name: str) -> None:
        self._api = api
        self._device_id = device_id
        self._device_name = device_name
        self._attr_is_on: bool | None = None

    @property
    def device_info(self):
        """Link to the existing Tuya device if present, otherwise create our own."""
        return {
            "identifiers": {("tuya", self._device_id)},
            "name": self._device_name,
            "manufacturer": "Tuya",
        }


class TuyaSmartLockOnlineSensor(TuyaSmartLockBinarySensor):
    """Online state sensor for a Tuya smart lock."""

    _attr_name = "Online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, api, device_id: str, device_name: str) -> None:
        super().__init__(api, device_id, device_name)
        self._attr_unique_id = f"smart_conlock_tuya_{device_id}_online"

    async def async_update(self) -> None:
        """Fetch whether the lock is online."""
        self._attr_is_on = await self._api.async_get_device_online(self._device_id)


class TuyaSmartLockCallActiveSensor(TuyaSmartLockBinarySensor):
    """Video call/session state sensor for a Tuya smart lock."""

    _attr_name = "Call Active"
    _attr_icon = "mdi:video"

    def __init__(self, api, device_id: str, device_name: str) -> None:
        super().__init__(api, device_id, device_name)
        self._attr_unique_id = f"smart_conlock_tuya_{device_id}_call_active"
        self._request_state = {}

    @property
    def extra_state_attributes(self) -> dict:
        """Return raw DPs used to investigate call/session state."""
        return {
            "source": self._request_state.get("source"),
            "last_event_time": self._request_state.get("last_event_time"),
            "seconds_since_event": self._request_state.get("seconds_since_event"),
            "doorbell": self._request_state.get("doorbell"),
            "video_request_realtime": self._request_state.get(
                "video_request_realtime"
            ),
            "initiative_message_decoded": self._request_state.get(
                "initiative_message_decoded"
            ),
        }

    async def async_update(self) -> None:
        """Fetch whether a video call/session appears active."""
        self._request_state = await self._api.async_get_jtmspro_request_state(
            self._device_id
        )
        self._attr_is_on = self._request_state["active"]
