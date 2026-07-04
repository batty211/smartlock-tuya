"""Binary sensor entities for Smart (Con)lock tuya."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DEVICE_CATEGORY, CONF_DEVICE_ID, CONF_DEVICE_NAME, DOMAIN
from .runtime import SmartConlockRuntime


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

    runtime = data["runtime"]
    device_id = entry_data[CONF_DEVICE_ID]
    device_name = entry_data[CONF_DEVICE_NAME]

    async_add_entities(
        [
            TuyaSmartLockOnlineSensor(runtime, device_id, device_name),
            TuyaSmartLockCallActiveSensor(runtime, device_id, device_name),
        ],
        True,
    )


class TuyaSmartLockBinarySensor(BinarySensorEntity):
    """Base binary sensor for a Tuya smart lock."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        runtime: SmartConlockRuntime,
        device_id: str,
        device_name: str,
    ) -> None:
        self._runtime = runtime
        self._device_id = device_id
        self._device_name = device_name
        self._attr_is_on: bool | None = None
        self._unsub_runtime: CALLBACK_TYPE | None = None

    @property
    def device_info(self):
        """Link to the existing Tuya device if present, otherwise create our own."""
        return {
            "identifiers": {("tuya", self._device_id)},
            "name": self._device_name,
            "manufacturer": "Tuya",
        }

    async def async_added_to_hass(self) -> None:
        """Subscribe to runtime state changes."""
        self._unsub_runtime = self._runtime.async_add_listener(
            self._handle_runtime_update
        )
        self._sync_from_runtime()

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from runtime state changes."""
        if self._unsub_runtime:
            self._unsub_runtime()
            self._unsub_runtime = None

    def _handle_runtime_update(self) -> None:
        """Handle runtime state updates."""
        self._sync_from_runtime()
        self.async_write_ha_state()

    def _sync_from_runtime(self) -> None:
        """Sync entity state from runtime."""
        raise NotImplementedError


class TuyaSmartLockOnlineSensor(TuyaSmartLockBinarySensor):
    """Online state sensor for a Tuya smart lock."""

    _attr_name = "Online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(
        self,
        runtime: SmartConlockRuntime,
        device_id: str,
        device_name: str,
    ) -> None:
        super().__init__(runtime, device_id, device_name)
        self._attr_unique_id = f"smart_conlock_tuya_{device_id}_online"

    def _sync_from_runtime(self) -> None:
        """Read online state from runtime."""
        self._attr_is_on = self._runtime.state.online


class TuyaSmartLockCallActiveSensor(TuyaSmartLockBinarySensor):
    """Video call/session state sensor for a Tuya smart lock."""

    _attr_name = "Video Call Request"
    _attr_icon = "mdi:video"

    def __init__(
        self,
        runtime: SmartConlockRuntime,
        device_id: str,
        device_name: str,
    ) -> None:
        super().__init__(runtime, device_id, device_name)
        self._attr_unique_id = f"smart_conlock_tuya_{device_id}_video_call_request"
        self._request_state = {}

    @property
    def extra_state_attributes(self) -> dict:
        """Return raw DPs used to investigate call/session state."""
        return {
            "diagnostic_status": self._request_state.get("diagnostic_status"),
            "last_error": self._request_state.get("last_error"),
            "report_log_error": self._request_state.get("report_log_error"),
            "report_log_count": self._request_state.get("report_log_count"),
            "request_window_seconds": self._request_state.get(
                "request_window_seconds"
            ),
            "source": self._request_state.get("source"),
            "last_event_time": self._request_state.get("last_event_time"),
            "seconds_since_event": self._request_state.get("seconds_since_event"),
            "request_expires_in": self._request_state.get("request_expires_in"),
            "last_event_code": self._request_state.get("last_event_code"),
            "last_event_value": self._request_state.get("last_event_value"),
            "doorbell": self._request_state.get("doorbell"),
            "video_request_realtime": self._request_state.get(
                "video_request_realtime"
            ),
            "initiative_message_decoded": self._request_state.get(
                "initiative_message_decoded"
            ),
        }

    def _sync_from_runtime(self) -> None:
        """Read request state from runtime."""
        self._request_state = self._runtime.state.request_state()
        self._attr_is_on = self._request_state["active"]
