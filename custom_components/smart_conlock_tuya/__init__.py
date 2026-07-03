"""Smart (Con)lock tuya integration."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ACCESS_ID,
    CONF_ACCESS_SECRET,
    CONF_API_REGION,
    CONF_DEVICE_CATEGORY,
    CONF_DEVICE_ID,
    DOMAIN,
)
from .tuya_api import TuyaCloudApi

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.LOCK, Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Smart (Con)lock tuya from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    api = TuyaCloudApi(
        access_id=entry.data[CONF_ACCESS_ID],
        access_secret=entry.data[CONF_ACCESS_SECRET],
        region=entry.data[CONF_API_REGION],
    )

    entry_data = dict(entry.data)
    device_info = None

    if not entry_data.get(CONF_DEVICE_CATEGORY):
        device_id = entry_data.get(CONF_DEVICE_ID)
        if device_id:
            try:
                device_info = await api.async_get_device_info(device_id)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Could not fetch Tuya device info: %s", err)
                device_info = None
            if device_info:
                entry_data[CONF_DEVICE_CATEGORY] = device_info.get("category", "")

    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "entry_data": entry_data,
        "device_info": device_info,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
