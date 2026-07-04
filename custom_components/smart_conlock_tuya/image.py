"""Image entity for Smart (Con)lock tuya."""

from __future__ import annotations

from datetime import UTC, datetime
import logging
from typing import Any

import aiohttp

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DEVICE_CATEGORY, CONF_DEVICE_ID, CONF_DEVICE_NAME, DOMAIN
from .runtime import SmartConlockRuntime

_LOGGER = logging.getLogger(__name__)

IMAGE_CONTENT_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
URL_KEYS = {
    "url",
    "media_url",
    "file_url",
    "download_url",
    "signed_url",
    "image_url",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up image entities from config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    entry_data = data["entry_data"]

    if entry_data.get(CONF_DEVICE_CATEGORY) != "jtmspro":
        return

    async_add_entities(
        [
            TuyaSmartLockLatestImage(
                hass,
                data["api"],
                data["runtime"],
                entry_data[CONF_DEVICE_ID],
                entry_data[CONF_DEVICE_NAME],
            )
        ],
        False,
    )


class TuyaSmartLockLatestImage(ImageEntity):
    """Latest still image captured by a jtmspro video lock."""

    _attr_has_entity_name = True
    _attr_name = "Latest Image"
    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        api: Any,
        runtime: SmartConlockRuntime,
        device_id: str,
        device_name: str,
    ) -> None:
        super().__init__(hass)
        self._api = api
        self._runtime = runtime
        self._device_id = device_id
        self._device_name = device_name
        self._attr_unique_id = f"smart_conlock_tuya_{device_id}_latest_image"
        self._attr_image_url: str | None = None
        self._attr_image_last_updated: datetime | None = None
        self._unsub_runtime: CALLBACK_TYPE | None = None
        self._last_file_signature: str | None = None
        self._latest_media_result: Any = None
        self._latest_resource_path: str | None = None
        self._latest_media_error: str | None = None
        self._refreshing = False

    @property
    def device_info(self):
        """Link to the existing Tuya device if present, otherwise create our own."""
        return {
            "identifiers": {("tuya", self._device_id)},
            "name": self._device_name,
            "manufacturer": "Tuya",
        }

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return media diagnostics."""
        return {
            "latest_resource_path": self._latest_resource_path,
            "latest_media_error": self._latest_media_error,
            "latest_media_result_keys": _safe_keys(self._latest_media_result),
            "image_url_available": self._attr_image_url is not None,
        }

    async def async_added_to_hass(self) -> None:
        """Subscribe to runtime state changes."""
        self._unsub_runtime = self._runtime.async_add_listener(
            self._handle_runtime_update
        )
        self._sync_from_runtime()
        if self._latest_resource_path:
            await self.async_update()

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from runtime state changes."""
        if self._unsub_runtime:
            self._unsub_runtime()
            self._unsub_runtime = None

    def _handle_runtime_update(self) -> None:
        """Refresh media URL when a new initiative message image appears."""
        changed = self._sync_from_runtime()
        if changed:
            self.hass.async_create_task(self._async_update_and_write_state())
        self.async_write_ha_state()

    async def _async_update_and_write_state(self) -> None:
        """Refresh media metadata and publish the new image state."""
        await self.async_update()
        self.async_write_ha_state()

    def _sync_from_runtime(self) -> bool:
        """Read the latest image evidence from runtime."""
        decoded = self._runtime.state.initiative_message_decoded
        resource_path = _latest_resource_path(decoded)
        signature = str(resource_path)
        changed = signature != self._last_file_signature
        self._last_file_signature = signature
        self._latest_resource_path = resource_path
        return changed

    async def async_update(self) -> None:
        """Fetch the latest Tuya media URL."""
        if self._refreshing:
            return
        if not self._latest_resource_path and self._attr_image_url is None:
            self._latest_media_error = "no_image_resource"
            return

        self._refreshing = True
        try:
            result = await self._api.async_get_latest_media_url(self._device_id, 1)
            self._latest_media_result = result
            image_url = _extract_url(result)
            if image_url:
                if image_url != self._attr_image_url:
                    self._attr_image_url = image_url
                    self._attr_image_last_updated = datetime.now(UTC)
                self._latest_media_error = None
            elif result is None:
                self._latest_media_error = "latest_media_unavailable"
            else:
                self._latest_media_error = "latest_media_url_not_found"
        except Exception as err:  # noqa: BLE001
            self._latest_media_error = str(err)
            _LOGGER.warning("Could not update latest Tuya lock image: %s", err)
        finally:
            self._refreshing = False

    async def async_image(self) -> bytes | None:
        """Return image bytes proxied through Home Assistant."""
        if not self._attr_image_url:
            return None

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self._attr_image_url) as resp:
                    if resp.status != 200:
                        self._latest_media_error = f"image_http_{resp.status}"
                        return None

                    content_type = resp.headers.get("content-type", "").split(";")[0]
                    if content_type and content_type.lower() not in IMAGE_CONTENT_TYPES:
                        self._latest_media_error = f"unexpected_content_type:{content_type}"

                    return await resp.read()
        except Exception as err:  # noqa: BLE001
            self._latest_media_error = str(err)
            _LOGGER.warning("Could not fetch latest Tuya lock image: %s", err)
            return None


def _latest_resource_path(decoded: dict[str, Any] | None) -> str | None:
    """Return the newest still-image resource path from initiative_message."""
    if not isinstance(decoded, dict):
        return None

    files = decoded.get("files")
    if not isinstance(files, list):
        return None

    for item in files:
        if not isinstance(item, list) or len(item) < 2:
            continue
        path = item[1]
        if isinstance(path, str) and path.lower().endswith((".jpg", ".jpeg", ".png")):
            return path

    return None


def _extract_url(value: Any) -> str | None:
    """Find a URL in common Tuya media response shapes."""
    if isinstance(value, str):
        return value if value.startswith(("http://", "https://")) else None

    if isinstance(value, dict):
        for key in URL_KEYS:
            url = _extract_url(value.get(key))
            if url:
                return url
        for item in value.values():
            url = _extract_url(item)
            if url:
                return url

    if isinstance(value, list):
        for item in value:
            url = _extract_url(item)
            if url:
                return url

    return None


def _safe_keys(value: Any) -> list[str] | None:
    """Return top-level keys for diagnostics."""
    if isinstance(value, dict):
        return sorted(str(key) for key in value)
    return None
