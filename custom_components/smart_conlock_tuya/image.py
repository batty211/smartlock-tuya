"""Image entity for Smart (Con)lock tuya."""

from __future__ import annotations

from datetime import UTC, datetime
import logging
from typing import Any

import aiohttp
from Crypto.Cipher import AES  # type: ignore
from Crypto.Util.Padding import unpad  # type: ignore

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DEVICE_CATEGORY, CONF_DEVICE_ID, CONF_DEVICE_NAME, DOMAIN
from .runtime import SmartConlockRuntime

_LOGGER = logging.getLogger(__name__)

STILL_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")
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
        self._attr_image_last_updated: datetime | None = None
        self._unsub_runtime: CALLBACK_TYPE | None = None
        self._last_file_signature: str | None = None
        self._latest_media_response: dict[str, Any] | None = None
        self._albums_media_response: dict[str, Any] | None = None
        self._latest_resource: dict[str, Any] | None = None
        self._latest_resource_path: str | None = None
        self._resolved_media: dict[str, Any] | None = None
        self._latest_media_error: str | None = None
        self._image_fetch_status: int | None = None
        self._image_fetch_content_type: str | None = None
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
            "latest_resource_bucket": _dict_get(self._latest_resource, "bucket"),
            "latest_resource_key_available": bool(
                _dict_get(self._latest_resource, "file_key")
            ),
            "latest_resource_expire": _dict_get(self._latest_resource, "expires_at"),
            "latest_file_id": _dict_get(self._latest_resource, "file_id"),
            "resolved_file_url_available": bool(
                _dict_get(self._resolved_media, "file_url")
            ),
            "resolved_file_path": _dict_get(self._resolved_media, "file_path"),
            "resolved_file_key_available": bool(
                _dict_get(self._resolved_media, "file_key")
            ),
            "resolved_media_source": _dict_get(self._resolved_media, "source"),
            "latest_media_response_code": _dict_get(
                self._latest_media_response, "code"
            ),
            "latest_media_response_msg": _dict_get(self._latest_media_response, "msg"),
            "latest_media_result_keys": _safe_keys(
                _dict_get(self._latest_media_response, "result")
            ),
            "albums_media_response_code": _dict_get(
                self._albums_media_response, "code"
            ),
            "albums_media_response_msg": _dict_get(self._albums_media_response, "msg"),
            "albums_media_result_keys": _safe_keys(
                _dict_get(self._albums_media_response, "result")
            ),
            "image_fetch_status": self._image_fetch_status,
            "image_fetch_content_type": self._image_fetch_content_type,
            "latest_media_error": self._latest_media_error,
            "image_url_available": bool(_dict_get(self._resolved_media, "file_url")),
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
        """Refresh media metadata when a new initiative image arrives."""
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
        resource = _latest_resource(decoded)
        resource_path = _dict_get(resource, "file_path")
        signature = str(resource)
        changed = signature != self._last_file_signature
        self._last_file_signature = signature
        self._latest_resource = resource
        self._latest_resource_path = resource_path
        if changed:
            self._resolved_media = None
            self._image_fetch_status = None
            self._image_fetch_content_type = None
        return changed

    async def async_update(self) -> None:
        """Resolve the latest Tuya media URL metadata."""
        if self._refreshing:
            return
        if not self._latest_resource:
            self._latest_media_error = "no_image_resource"
            return

        self._refreshing = True
        try:
            latest_response = await self._api.async_get_latest_media_response(
                self._device_id, 1
            )
            self._latest_media_response = latest_response
            media = _media_from_latest_response(
                latest_response,
                self._latest_resource,
            )

            if media is None:
                albums_response = await self._api.async_get_albums_media_response(
                    self._device_id
                )
                self._albums_media_response = albums_response
                media = _media_from_albums_response(
                    albums_response,
                    self._latest_resource,
                )

            if media:
                if media != self._resolved_media:
                    self._resolved_media = media
                    self._attr_image_last_updated = datetime.now(UTC)
                self._latest_media_error = None
            elif not latest_response.get("success"):
                self._latest_media_error = "latest_media_unavailable"
            else:
                self._latest_media_error = "matching_media_url_not_found"
        except Exception as err:  # noqa: BLE001
            self._latest_media_error = str(err)
            _LOGGER.warning("Could not update latest Tuya lock image: %s", err)
        finally:
            self._refreshing = False

    async def async_image(self) -> bytes | None:
        """Return decrypted image bytes proxied through Home Assistant."""
        if not self._resolved_media:
            await self.async_update()
        if not self._resolved_media:
            return None

        image_url = self._resolved_media.get("file_url")
        file_key = self._resolved_media.get("file_key")
        if not image_url:
            self._latest_media_error = "missing_file_url"
            return None
        if not file_key:
            self._latest_media_error = "missing_file_key"
            return None

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as resp:
                    self._image_fetch_status = resp.status
                    self._image_fetch_content_type = resp.headers.get(
                        "content-type", ""
                    ).split(";")[0]
                    if resp.status != 200:
                        self._latest_media_error = f"image_http_{resp.status}"
                        return None

                    encrypted = await resp.read()
                    if _looks_like_image(encrypted):
                        self._latest_media_error = None
                        return encrypted

                    image = _decrypt_tuya_media(encrypted, file_key)
                    if not _looks_like_image(image):
                        self._latest_media_error = "decrypted_bytes_not_image"
                        return None
                    self._latest_media_error = None
                    return image
        except Exception as err:  # noqa: BLE001
            self._latest_media_error = str(err)
            _LOGGER.warning("Could not fetch latest Tuya lock image: %s", err)
            return None


def _latest_resource(decoded: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the newest still-image resource tuple from initiative_message."""
    if not isinstance(decoded, dict):
        return None

    files = decoded.get("files")
    if not isinstance(files, list):
        return None

    ext = decoded.get("ext")
    for item in files:
        if not isinstance(item, list) or len(item) < 2:
            continue
        path = item[1]
        if isinstance(path, str) and path.lower().endswith(STILL_IMAGE_SUFFIXES):
            return {
                "bucket": item[0] if len(item) > 0 else None,
                "file_path": path,
                "file_key": item[2] if len(item) > 2 else None,
                "expires_at": item[3] if len(item) > 3 else None,
                "file_id": _dict_get(ext, "fileId") or _dict_get(ext, "file_id"),
                "event_type": _dict_get(ext, "type"),
            }

    return None


def _media_from_latest_response(
    response: dict[str, Any],
    resource: dict[str, Any],
) -> dict[str, Any] | None:
    """Return resolved media from Tuya latest-media response when it matches."""
    if not response.get("success"):
        return None

    result = response.get("result")
    if not isinstance(result, dict):
        return None

    media = _normalize_media(result, "latest_media")
    if not media:
        return None

    resource_path = _dict_get(resource, "file_path")
    media_path = media.get("file_path")
    if resource_path and media_path and resource_path != media_path:
        return None

    if not media.get("file_path"):
        media["file_path"] = resource_path
    if not media.get("file_key"):
        media["file_key"] = _dict_get(resource, "file_key")
    if not media.get("bucket"):
        media["bucket"] = _dict_get(resource, "bucket")

    return media if media.get("file_url") and media.get("file_key") else None


def _media_from_albums_response(
    response: dict[str, Any],
    resource: dict[str, Any],
) -> dict[str, Any] | None:
    """Return resolved media from albums-media response when it matches."""
    if not response.get("success"):
        return None

    result = response.get("result")
    if not isinstance(result, dict):
        return None

    album_list = result.get("album_list") or result.get("albumList")
    if not isinstance(album_list, list):
        return None

    candidates = []
    for item in album_list:
        if not isinstance(item, dict):
            continue
        media = _normalize_media(item, "albums_media")
        if media and _album_matches_resource(media, resource):
            candidates.append(media)

    if not candidates:
        return None

    candidates.sort(key=lambda item: int(item.get("upload_time") or 0), reverse=True)
    media = candidates[0]
    if not media.get("file_key"):
        media["file_key"] = _dict_get(resource, "file_key")
    return media if media.get("file_url") and media.get("file_key") else None


def _normalize_media(value: dict[str, Any], source: str) -> dict[str, Any] | None:
    """Normalize Tuya snake_case and camelCase media fields."""
    file_url = value.get("file_url") or value.get("fileUrl") or _extract_url(value)
    if not file_url:
        return None

    return {
        "source": source,
        "file_url": file_url,
        "file_key": value.get("file_key") or value.get("fileKey"),
        "file_path": value.get("file_path")
        or value.get("filePath")
        or _url_path(file_url),
        "bucket": value.get("bucket") or value.get("mediaBucket"),
        "file_id": value.get("file_id") or value.get("fileId"),
        "event_type": value.get("event_type") or value.get("eventType"),
        "upload_time": value.get("upload_time") or value.get("uploadTime"),
    }


def _album_matches_resource(
    media: dict[str, Any],
    resource: dict[str, Any],
) -> bool:
    """Return whether an albums-media item likely represents the resource."""
    resource_file_id = _dict_get(resource, "file_id")
    if resource_file_id and str(media.get("file_id")) == str(resource_file_id):
        return True

    resource_path = _dict_get(resource, "file_path")
    media_path = media.get("file_path") or ""
    if resource_path and (
        media_path == resource_path or media_path.endswith(str(resource_path))
    ):
        return True

    resource_event_type = _dict_get(resource, "event_type")
    if resource_event_type and str(media.get("event_type")) == str(
        resource_event_type
    ):
        return True

    return False


def _extract_url(value: Any) -> str | None:
    """Find a URL in common Tuya media response shapes."""
    if isinstance(value, str):
        return value if value.startswith(("http://", "https://")) else None

    if isinstance(value, dict):
        for key in URL_KEYS:
            url = _extract_url(value.get(key))
            if url:
                return url

    return None


def _decrypt_tuya_media(data: bytes, file_key: str) -> bytes:
    """Decrypt Tuya doorlock media bytes using AES/CBC/PKCS5Padding."""
    if len(data) <= 64:
        raise ValueError("encrypted media is too short")

    key = file_key.encode("utf8")
    iv = data[4:20]
    payload = data[64:]
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return unpad(cipher.decrypt(payload), AES.block_size)


def _looks_like_image(data: bytes) -> bool:
    """Return whether bytes look like a common still image format."""
    return data.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"RIFF"))


def _url_path(url: str | None) -> str | None:
    """Return a URL path from a signed media URL."""
    if not isinstance(url, str) or "://" not in url:
        return None
    path = url.split("://", 1)[1].split("/", 1)
    return f"/{path[1].split('?', 1)[0]}" if len(path) == 2 else None


def _dict_get(value: Any, key: str) -> Any:
    """Read a dict value safely."""
    return value.get(key) if isinstance(value, dict) else None


def _safe_keys(value: Any) -> list[str] | None:
    """Return top-level keys for diagnostics."""
    if isinstance(value, dict):
        return sorted(str(key) for key in value)
    return None
