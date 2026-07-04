"""Lock entity for Smart (Con)lock tuya."""

from __future__ import annotations

import logging
import time

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import slugify

from .const import (
    CONF_DEVICE_CATEGORY,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_DEVICE_RELOCK_DELAY,
    DOMAIN,
)
from .runtime import SmartConlockRuntime

_LOGGER = logging.getLogger(__name__)

LOCK_OPERATION_EVENT = "smart_conlock_tuya_lock_operation"
REMOTE_UNLOCK_CONTROL_ROLE = "remote_unlock_control"
PHYSICAL_STATUS_ROLE = "manual_physical_status"


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

    device_auto_lock_time = await api.async_get_auto_lock_time(device_id)
    configured_relock_delay = _configured_relock_delay(entry)
    relock_delay = device_auto_lock_time or configured_relock_delay
    relock_delay_source = (
        "tuya_auto_lock_time"
        if device_auto_lock_time is not None
        else "configured_device_relock_delay"
        if configured_relock_delay is not None
        else None
    )

    entities: list[LockEntity] = [
        TuyaSmartLock(
            api,
            runtime,
            device_id,
            device_name,
            device_category,
            relock_delay,
            relock_delay_source,
        )
    ]
    if device_category == "jtmspro":
        entities.append(
            TuyaSmartLockPhysicalStatus(runtime, device_id, device_name)
        )

    async_add_entities(
        entities,
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
        relock_delay: int | None,
        relock_delay_source: str | None,
    ) -> None:
        self._api = api
        self._runtime = runtime
        self._device_id = device_id
        self._device_category = device_category
        self._relock_delay = relock_delay
        self._relock_delay_source = relock_delay_source
        self._attr_unique_id = f"smart_conlock_tuya_{device_id}"
        self._attr_is_locked = True if device_category == "jtmspro" else None
        self._attr_is_locking = False
        self._attr_is_unlocking = False
        self._attr_available = device_category != "jtmspro"
        self._attr_should_poll = device_category != "jtmspro"
        self._device_name = device_name
        self._command_state_source = "init"
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
            "entity_role": REMOTE_UNLOCK_CONTROL_ROLE,
            "physical_status_entity": (
                f"lock.{slugify(self._device_name)}_physical_status"
            ),
            "unlock_available": self._unlock_available(),
            "lock_available": False,
            "tuya_lock_api_supported": False,
            "command_state_source": self._command_state_source,
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
            "state_source": self._lock_state.get("state_source"),
            "state_confidence": self._lock_state.get("state_confidence"),
            "lock_report_log_error": self._lock_state.get("lock_report_log_error"),
            "lock_report_log_count": self._lock_state.get("lock_report_log_count"),
            "last_lock_operation": self._lock_state.get("last_lock_operation"),
            "relock_delay": self._relock_delay,
            "relock_delay_source": self._relock_delay_source,
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
        if self._device_category == "jtmspro":
            self._reset_remote_unlock_control("manual_reset")
            self.async_write_ha_state()
            return

        self._attr_is_locking = True
        self.async_write_ha_state()

        success = False
        if await self._async_can_lock():
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
            if self._device_category == "jtmspro":
                self._record_remote_unlock_success()
            else:
                self._record_successful_operation("unlocked", False)
        self.async_write_ha_state()

        if success and self._relock_delay and self._device_category != "jtmspro":
            delay = self._relock_delay + 1
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

    async def _async_can_lock(self) -> bool:
        """Check whether locking is allowed for this lock."""
        if self._device_category != "jtmspro":
            return True

        return False

    def _sync_from_runtime(self) -> tuple[bool | None, bool | None]:
        """Sync lock availability from the shared runtime."""
        if self._device_category != "jtmspro":
            self._attr_available = True
            return True, True

        if self._runtime is None:
            self._attr_available = False
            self._request_state = {
                "active": False,
                "diagnostic_status": "no_runtime",
            }
            self._lock_state = {}
            return None, False

        self._request_state = self._runtime.state.request_state()
        self._lock_state = self._runtime.state.lock_state()
        online = self._runtime.state.online
        call_active = self._request_state["active"]

        if call_active is not True:
            self._reset_remote_unlock_control("call_inactive")
        else:
            self._attr_is_locked = True
        self._attr_available = online is True and call_active is True
        return online, call_active

    def _unlock_available(self) -> bool:
        """Return whether the unlock command should be accepted."""
        return self._runtime is not None and (
            self._runtime.state.online is True
            and self._request_state.get("active") is True
        )

    def _lock_available(self) -> bool:
        """Return whether the lock command should be accepted."""
        if self._device_category == "jtmspro":
            return False
        return self._runtime is not None and self._runtime.state.online is True

    def _reset_remote_unlock_control(self, source: str) -> None:
        """Reset jtmspro control state so Home Assistant offers Unlock next."""
        self._attr_is_locked = True
        self._attr_is_locking = False
        self._attr_is_unlocking = False
        self._command_state_source = source

    def _record_remote_unlock_success(self) -> None:
        """Record a successful jtmspro unlock command."""
        self._attr_is_locked = True
        self._command_state_source = "unlock_success"
        event_time = int(time.time() * 1000)
        self._lock_state = {
            **self._lock_state,
            "locked": False,
            "state_source": "remote_unlock_control",
            "state_confidence": "command_assumed",
            "last_lock_operation": {
                "action": "unlocked",
                "source": "remote_unlock_control",
                "event_time": event_time,
                "value": None,
            },
        }
        if self._runtime is not None:
            self._runtime.record_home_assistant_operation(
                "unlocked",
                False,
                "remote_unlock_control",
            )
        self.hass.bus.async_fire(
            LOCK_OPERATION_EVENT,
            {
                "entity_id": self.entity_id,
                "device_id": self._device_id,
                "device_name": self._device_name,
                "action": "unlocked",
                "source": "remote_unlock_control",
                "event_time": event_time,
            },
        )

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
        self._lock_state = {
            **self._lock_state,
            "locked": locked,
            "state_source": source,
            "state_confidence": "command_assumed",
            "last_lock_operation": operation,
        }

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
            source=self._relock_delay_source or "configured_device_relock_delay",
        )
        self.async_write_ha_state()


def _configured_relock_delay(entry: ConfigEntry) -> int | None:
    """Return the user-configured physical relock delay."""
    value = str(entry.options.get(CONF_DEVICE_RELOCK_DELAY, "off")).lower()
    if value == "off":
        return None
    try:
        delay = int(value)
    except ValueError:
        return None
    return delay if delay in {5, 10, 15} else None


class TuyaSmartLockPhysicalStatus(LockEntity, RestoreEntity):
    """Manual physical lock status for a jtmspro lock."""

    _attr_has_entity_name = True
    _attr_name = "Physical Status"
    _attr_should_poll = False

    def __init__(
        self,
        runtime: SmartConlockRuntime | None,
        device_id: str,
        device_name: str,
    ) -> None:
        self._runtime = runtime
        self._device_id = device_id
        self._device_name = device_name
        self._attr_unique_id = f"smart_conlock_tuya_{device_id}_physical_status"
        self._attr_is_locked = None
        self._state_source = "unknown"
        self._last_manual_update: int | None = None
        self._lock_state: dict = {}
        self._unsub_runtime: CALLBACK_TYPE | None = None

    @property
    def device_info(self):
        """Link to the existing Tuya device."""
        return {
            "identifiers": {("tuya", self._device_id)},
            "name": self._device_name,
            "manufacturer": "Tuya",
        }

    @property
    def extra_state_attributes(self) -> dict:
        """Return physical status diagnostics."""
        return {
            "entity_role": PHYSICAL_STATUS_ROLE,
            "state_source": self._state_source,
            "last_manual_update": self._last_manual_update,
            "lock_motor_state": self._lock_state.get("lock_motor_state"),
            "state_confidence": self._lock_state.get("state_confidence"),
        }

    async def async_added_to_hass(self) -> None:
        """Restore manual state and subscribe to runtime updates."""
        last_state = await self.async_get_last_state()
        if last_state is not None:
            if last_state.state == "locked":
                self._attr_is_locked = True
                self._state_source = "restored"
            elif last_state.state == "unlocked":
                self._attr_is_locked = False
                self._state_source = "restored"

        if self._runtime is not None:
            self._unsub_runtime = self._runtime.async_add_listener(
                self._handle_runtime_update
            )
            self._sync_from_runtime()

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from runtime updates."""
        if self._unsub_runtime:
            self._unsub_runtime()
            self._unsub_runtime = None

    def _handle_runtime_update(self) -> None:
        """Handle runtime state updates."""
        self._sync_from_runtime()
        self.async_write_ha_state()

    def _sync_from_runtime(self) -> None:
        """Sync trusted physical state and successful remote unlocks."""
        if self._runtime is None:
            return

        self._lock_state = self._runtime.state.lock_state()
        state_confidence = self._lock_state.get("state_confidence")
        state_source = self._lock_state.get("state_source")
        last_operation = self._lock_state.get("last_lock_operation") or {}

        locked = self._lock_state.get("locked")
        if state_confidence == "physical_dp" and locked is not None:
            self._attr_is_locked = locked
            self._state_source = (
                state_source or "tuya_lock_motor_state"
            )
            return

        if (
            state_confidence == "command_assumed"
            and state_source == "remote_unlock_control"
            and locked is False
            and last_operation.get("source") == "remote_unlock_control"
            and self._operation_is_newer_than_manual_update(last_operation)
        ):
            self._attr_is_locked = False
            self._state_source = "remote_unlock_control"

    async def async_lock(self, **kwargs) -> None:
        """Manually mark the physical lock as locked."""
        self._set_manual_state(True)

    async def async_unlock(self, **kwargs) -> None:
        """Manually mark the physical lock as unlocked."""
        self._set_manual_state(False)

    def _set_manual_state(self, locked: bool) -> None:
        """Set the manual physical status."""
        self._attr_is_locked = locked
        self._state_source = "manual"
        self._last_manual_update = int(time.time() * 1000)
        self.async_write_ha_state()

    def _operation_is_newer_than_manual_update(self, operation: dict) -> bool:
        """Return whether a runtime operation should override manual status."""
        if self._last_manual_update is None:
            return True

        event_time = operation.get("event_time")
        if event_time is None:
            return False

        try:
            return int(event_time) > self._last_manual_update
        except (TypeError, ValueError):
            return False
