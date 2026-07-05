"""Sensor entities for Smart (Con)lock tuya."""

from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from .const import CONF_DEVICE_ID, CONF_DEVICE_NAME, DOMAIN
from .runtime import SmartConlockRuntime

BATTERY_REFRESH_INTERVAL = timedelta(days=1)

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
    runtime = data.get("runtime")
    entry_data = data["entry_data"]
    device_id = entry_data[CONF_DEVICE_ID]
    device_name = entry_data[CONF_DEVICE_NAME]

    async_add_entities(
        [TuyaSmartLockBatterySensor(hass, api, runtime, device_id, device_name)],
        False,
    )


class TuyaSmartLockBatterySensor(SensorEntity):
    """Battery state sensor for a Tuya smart lock."""

    _attr_has_entity_name = True
    _attr_name = "Battery"
    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        api,
        runtime: SmartConlockRuntime | None,
        device_id: str,
        device_name: str,
    ) -> None:
        self.hass = hass
        self._api = api
        self._runtime = runtime
        self._device_id = device_id
        self._device_name = device_name
        self._battery_state: str | None = None
        self._attr_unique_id = f"smart_conlock_tuya_{device_id}_battery"
        self._last_call_active: bool | None = None
        self._refreshing = False
        self._unsub_daily: CALLBACK_TYPE | None = None
        self._unsub_runtime: CALLBACK_TYPE | None = None

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

    async def async_added_to_hass(self) -> None:
        """Schedule sparse battery refreshes and watch active call transitions."""
        self._unsub_daily = async_track_time_interval(
            self.hass,
            self._async_daily_refresh,
            BATTERY_REFRESH_INTERVAL,
        )
        self.async_on_remove(self._cancel_daily_refresh)

        if self._runtime is not None:
            self._last_call_active = self._runtime.state.request_active
            self._unsub_runtime = self._runtime.async_add_listener(
                self._handle_runtime_update
            )
            self.async_on_remove(self._cancel_runtime_listener)

        await self.async_update()

    async def _async_daily_refresh(self, _now: Any = None) -> None:
        """Refresh battery state once per day."""
        await self._async_update_and_write_state()

    async def _async_update_and_write_state(self) -> None:
        """Refresh battery state and publish it to Home Assistant."""
        await self.async_update()
        self.async_write_ha_state()

    @callback
    def _handle_runtime_update(self) -> None:
        """Refresh battery when a video call/request becomes active."""
        if self._runtime is None:
            return

        call_active = self._runtime.state.request_active
        became_active = call_active is True and self._last_call_active is not True
        self._last_call_active = call_active

        if became_active:
            self.hass.async_create_task(self._async_update_and_write_state())

    @callback
    def _cancel_daily_refresh(self) -> None:
        """Cancel the daily refresh timer."""
        if self._unsub_daily:
            self._unsub_daily()
            self._unsub_daily = None

    @callback
    def _cancel_runtime_listener(self) -> None:
        """Unsubscribe from runtime updates."""
        if self._unsub_runtime:
            self._unsub_runtime()
            self._unsub_runtime = None

    async def async_update(self) -> None:
        """Fetch the latest battery state from Tuya Cloud."""
        if self._refreshing:
            return

        self._refreshing = True
        try:
            self._battery_state = await self._api.async_get_battery_state(
                self._device_id
            )
        finally:
            self._refreshing = False
