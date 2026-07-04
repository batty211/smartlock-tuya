"""Lock entity for Smart (Con)lock tuya."""

from __future__ import annotations

import logging
import time

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DEVICE_CATEGORY, CONF_DEVICE_ID, CONF_DEVICE_NAME, DOMAIN
from .runtime import SmartConlockRuntime

_LOGGER = logging.getLogger(__name__)

DEFAULT_AUTO_LOCK_DELAY = 3
LOCK_OPERATION_EVENT = "smart_conlock_tuya_lock_operation"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up lock entity from config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    api = data["api"]
    runtime = data.get("runtime")
    entry_data = data["entry_data"]
    device_id = entry_data[CONF_DEVICE_ID]
    device_name = entry_data[CONF_DEVICE_NAME]
    device_category = entry_data.get(CONF_DEVICE_CATEGORY)

    # Read auto_lock_time from device
    auto_lock_time = await api.async_get_auto_lock_time(device_id)
    if auto_lock_time is None:
        auto_lock_time = DEFAULT_AUTO_LOCK_DELAY

    async_add_entities(
        [
            TuyaSmartLock(
                api,
                runtime,
                device_id,
                device_name,
                device_category,
                auto_lock_time,
            )
        ],
        True,
    )


class TuyaSmartLock(LockEntity):
    """Lock entity that controls a Tuya smart lock via Cloud API."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(
        self,
        api,
        runtime: SmartConlockRuntime | None,
        device_id: str,
        device_name: str,
        device_category: str | None,
        auto_lock_time: int,
    ) -> None:
        self._api = api
        self._runtime = runtime
        self._device_id = device_id
        self._device_category = device_category
        self._auto_lock_time = auto_lock_time
        self._attr_unique_id = f"smart_conlock_tuya_{device_id}"
        self._attr_is_locked = True
        self._attr_is_locking = False
        self._attr_is_unlocking = False
        self._attr_available = device_category != "jtmspro"
        self._attr_should_poll = device_category != "jtmspro"
        self._device_name = device_name
        self._request_state = {}
        self._lock_state = {}
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
    def extra_state_attributes(self) -> dict:
        """Return jtmspro unlock eligibility details."""
        if self._device_category != "jtmspro":
            return {}

        self._sync_from_runtime()
        return {
            "unlock_available": self._attr_available,
            "call_active": self._request_state.get("active"),
            "call_active_diagnostic_status": self._request_state.get(
                "diagnostic_status"
            ),
            "call_active_report_log_error": self._request_state.get(
                "report_log_error"
            ),
            "call_active_source": self._request_state.get("source"),
            "call_active_seconds_since_event": self._request_state.get(
                "seconds_since_event"
            ),
            "lock_motor_state": self._lock_state.get("lock_motor_state"),
            "lock_report_log_error": self._lock_state.get("lock_report_log_error"),
            "lock_report_log_count": self._lock_state.get("lock_report_log_count"),
            "last_lock_operation": self._lock_state.get("last_lock_operation"),
        }

    async def async_update(self) -> None:
        """Update lock availability."""
        if self._device_category != "jtmspro":
            self._attr_available = True
            locked = await self._api.async_get_lock_state(self._device_id)
            if locked is not None:
                self._attr_is_locked = locked
            return

        if self._runtime is not None:
            await self._runtime.async_refresh_fallback()
        self._sync_from_runtime()

    async def async_added_to_hass(self) -> None:
        """Subscribe to runtime state changes."""
        if self._runtime is None:
            return
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

    async def async_lock(self, **kwargs) -> None:
        """Lock the door."""
        self._attr_is_locking = True
        self.async_write_ha_state()

        success = await self._api.async_lock(self._device_id)

        self._attr_is_locking = False
        if success:
            self._record_successful_operation("locked", True)
        self.async_write_ha_state()

    async def async_unlock(self, **kwargs) -> None:
        """Unlock the door."""
        self._attr_is_unlocking = True
        self.async_write_ha_state()

        success = False
        if await self._async_can_unlock():
            success = await self._api.async_unlock(self._device_id)

        self._attr_is_unlocking = False
        if success:
            self._record_successful_operation("unlocked", False)
        self.async_write_ha_state()

        if success:
            # Re-lock after auto_lock_time + 1s buffer
            delay = self._auto_lock_time + 1
            self.hass.loop.call_later(delay, self._set_locked)

    async def _async_can_unlock(self) -> bool:
        """Check whether unlocking is allowed for this lock."""
        if self._device_category != "jtmspro":
            return True

        online, call_active = self._sync_from_runtime()
        if online is not True:
            _LOGGER.warning(
                "Refusing to unlock %s because jtmspro device is not online",
                self._device_id,
            )
            return False

        if call_active is not True:
            _LOGGER.warning(
                "Refusing to unlock %s because no active video call was detected",
                self._device_id,
            )
            return False

        return True

    def _sync_from_runtime(self) -> tuple[bool | None, bool | None]:
        """Sync lock availability from the shared runtime."""
        if self._device_category != "jtmspro":
            self._attr_available = True
            return True, True

        if self._runtime is None:
            self._attr_available = False
            self._request_state = {"active": False, "diagnostic_status": "no_runtime"}
            self._lock_state = {}
            return None, False

        self._request_state = self._runtime.state.request_state()
        self._lock_state = self._runtime.state.lock_state()
        online = self._runtime.state.online
        call_active = self._request_state["active"]
        locked = self._lock_state.get("locked")
        if locked is not None:
            self._attr_is_locked = locked
        self._attr_available = online is True and call_active is True
        return online, call_active

    def _record_successful_operation(
        self,
        action: str,
        locked: bool,
        source: str = "home_assistant",
    ) -> None:
        """Record a successful lock operation and emit a Home Assistant event."""
        self._attr_is_locked = locked
        event_time = int(time.time() * 1000)
        operation = {
            "action": action,
            "source": source,
            "event_time": event_time,
            "value": None,
        }
        self._lock_state = {**self._lock_state, "last_lock_operation": operation}

        if self._runtime is not None:
            self._runtime.record_home_assistant_operation(action, locked, source)

        self.hass.bus.async_fire(
            LOCK_OPERATION_EVENT,
            {
                "entity_id": self.entity_id,
                "device_id": self._device_id,
                "device_name": self._device_name,
                "action": action,
                "source": source,
                "event_time": event_time,
            },
        )

    def _set_locked(self) -> None:
        """Reset state to locked after auto-lock delay."""
        self._record_successful_operation(
            "locked",
            True,
            source="auto_lock_timer_estimate",
        )
        self.async_write_ha_state()
