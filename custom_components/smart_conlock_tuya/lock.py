"""Lock entity for Smart (Con)lock tuya."""

from __future__ import annotations

import logging

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DEVICE_CATEGORY, CONF_DEVICE_ID, CONF_DEVICE_NAME, DOMAIN

_LOGGER = logging.getLogger(__name__)

DEFAULT_AUTO_LOCK_DELAY = 3


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up lock entity from config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    api = data["api"]
    entry_data = data["entry_data"]
    device_id = entry_data[CONF_DEVICE_ID]
    device_name = entry_data[CONF_DEVICE_NAME]
    device_category = entry_data.get(CONF_DEVICE_CATEGORY)

    # Read auto_lock_time from device
    auto_lock_time = await api.async_get_auto_lock_time(device_id)
    if auto_lock_time is None:
        auto_lock_time = DEFAULT_AUTO_LOCK_DELAY

    async_add_entities(
        [TuyaSmartLock(api, device_id, device_name, device_category, auto_lock_time)],
        True,
    )


class TuyaSmartLock(LockEntity):
    """Lock entity that controls a Tuya smart lock via Cloud API."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(
        self,
        api,
        device_id: str,
        device_name: str,
        device_category: str | None,
        auto_lock_time: int,
    ) -> None:
        self._api = api
        self._device_id = device_id
        self._device_category = device_category
        self._auto_lock_time = auto_lock_time
        self._attr_unique_id = f"smart_conlock_tuya_{device_id}"
        self._attr_is_locked = True
        self._attr_is_locking = False
        self._attr_is_unlocking = False
        self._attr_available = device_category != "jtmspro"
        self._attr_should_poll = device_category == "jtmspro"
        self._device_name = device_name
        self._request_state = {}

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

        return {
            "unlock_available": self._attr_available,
            "call_active": self._request_state.get("active"),
            "call_active_source": self._request_state.get("source"),
            "call_active_seconds_since_event": self._request_state.get(
                "seconds_since_event"
            ),
        }

    async def async_update(self) -> None:
        """Update jtmspro unlock availability."""
        if self._device_category != "jtmspro":
            self._attr_available = True
            return

        await self._async_update_unlock_availability()

    async def async_lock(self, **kwargs) -> None:
        """Lock the door."""
        self._attr_is_locking = True
        self.async_write_ha_state()

        success = await self._api.async_lock(self._device_id)

        self._attr_is_locking = False
        if success:
            self._attr_is_locked = True
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
            self._attr_is_locked = False
        self.async_write_ha_state()

        if success:
            # Re-lock after auto_lock_time + 1s buffer
            delay = self._auto_lock_time + 1
            self.hass.loop.call_later(delay, self._set_locked)

    async def _async_can_unlock(self) -> bool:
        """Check whether unlocking is allowed for this lock."""
        if self._device_category != "jtmspro":
            return True

        online, call_active = await self._async_update_unlock_availability()
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

    async def _async_update_unlock_availability(self) -> tuple[bool | None, bool | None]:
        """Fetch and store whether unlock should be available for a jtmspro lock."""
        online = await self._api.async_get_device_online(self._device_id)
        self._request_state = await self._api.async_get_jtmspro_request_state(
            self._device_id
        )
        call_active = self._request_state["active"]
        self._attr_available = online is True and call_active is True
        return online, call_active

    def _set_locked(self) -> None:
        """Reset state to locked after auto-lock delay."""
        self._attr_is_locked = True
        self.async_write_ha_state()
