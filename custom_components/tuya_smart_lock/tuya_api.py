"""Tuya Cloud API client for Smart Lock operations."""

import hashlib
import hmac
import json
import logging
import time
from typing import Any

import aiohttp

from .const import (
    ALBUMS_MEDIA_ENDPOINT,
    API_REGIONS,
    DEVICE_DETAILS_ENDPOINT,
    DOOR_OPERATE_ENDPOINT,
    LATEST_MEDIA_ENDPOINT,
    LOCK_CATEGORIES,
    REMOTE_UNLOCKS_ENDPOINT,
    SPECIFICATIONS_ENDPOINT,
    STATUS_ENDPOINT,
    STREAM_ALLOCATE_ENDPOINT,
    TICKET_ENDPOINT,
    WEBRTC_CONFIG_ENDPOINT,
)

_LOGGER = logging.getLogger(__name__)

TRUTHY_VALUES = {"1", "true", "on", "active"}
FALSY_VALUES = {"0", "false", "off", "inactive"}


class TuyaCloudApi:
    """Tuya Cloud API client for lock operations."""

    def __init__(self, access_id: str, access_secret: str, region: str = "eu") -> None:
        self._access_id = access_id
        self._access_secret = access_secret
        self._base_url = f"https://{API_REGIONS[region]}"
        self._token: str | None = None
        self._token_expiry: float = 0
        self._uid: str | None = None

    async def _ensure_token(self) -> None:
        """Get or refresh the access token."""
        if self._token and time.time() < self._token_expiry:
            return

        url = f"{self._base_url}/v1.0/token?grant_type=1"
        t = str(int(time.time() * 1000))

        string_to_sign = (
            "GET\n"
            + hashlib.sha256(b"").hexdigest()
            + "\n\n"
            + "/v1.0/token?grant_type=1"
        )
        sign_str = self._access_id + t + string_to_sign
        sign = hmac.new(
            self._access_secret.encode(),
            sign_str.encode(),
            hashlib.sha256,
        ).hexdigest().upper()

        headers = {
            "client_id": self._access_id,
            "sign": sign,
            "t": t,
            "sign_method": "HMAC-SHA256",
            "secret": self._access_secret,
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                data = await resp.json()

        if not data.get("success"):
            _LOGGER.error("Failed to get Tuya token: %s", data.get("msg"))
            raise ConnectionError(f"Tuya token error: {data.get('msg')}")

        result = data["result"]
        self._token = result["access_token"]
        self._token_expiry = time.time() + result["expire_time"] - 60
        self._uid = result.get("uid")

    def _sign_request(self, method: str, path: str, body: str = "") -> dict:
        """Build signed headers for a Tuya API request."""
        t = str(int(time.time() * 1000))
        content_hash = hashlib.sha256(body.encode()).hexdigest()
        string_to_sign = f"{method}\n{content_hash}\n\n{path}"
        sign_str = self._access_id + self._token + t + string_to_sign
        sign = hmac.new(
            self._access_secret.encode(),
            sign_str.encode(),
            hashlib.sha256,
        ).hexdigest().upper()

        return {
            "client_id": self._access_id,
            "access_token": self._token,
            "sign": sign,
            "t": t,
            "sign_method": "HMAC-SHA256",
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        """Make a signed request to the Tuya API."""
        await self._ensure_token()
        url = f"{self._base_url}{path}"
        body_str = json.dumps(body) if body else ""
        headers = self._sign_request(method, path, body_str)

        async with aiohttp.ClientSession() as session:
            if method == "GET":
                async with session.get(url, headers=headers) as resp:
                    return await resp.json()
            else:
                async with session.post(url, headers=headers, data=body_str) as resp:
                    return await resp.json()

    async def async_test_credentials(self) -> bool:
        """Test if the credentials are valid."""
        try:
            self._token = None
            self._token_expiry = 0
            await self._ensure_token()
            return True
        except ConnectionError:
            return False

    async def async_discover_devices(self) -> list[dict]:
        """Discover lock devices linked to this account."""
        await self._ensure_token()

        # Use the associated-users endpoint which lists all devices linked via the app
        resp = await self._request("GET", "/v1.0/iot-01/associated-users/devices")

        if not resp.get("success"):
            _LOGGER.error("Failed to list devices: %s", resp.get("msg"))
            return []

        # Response structure: result.devices (list)
        result = resp.get("result", {})
        all_devices = result.get("devices", result) if isinstance(result, dict) else result

        devices = []
        for device in all_devices:
            category = device.get("category", "")
            if category in LOCK_CATEGORIES:
                devices.append({
                    "id": device["id"],
                    "name": device.get("name", device["id"]),
                    "category": category,
                    "model": device.get("model", ""),
                    "product_name": device.get("product_name", ""),
                })

        return devices

    async def async_check_remote_unlock(self, device_id: str) -> bool:
        """Check if remote unlock without password is enabled."""
        path = REMOTE_UNLOCKS_ENDPOINT.format(device_id=device_id)
        resp = await self._request("GET", path)

        if not resp.get("success"):
            _LOGGER.warning("Could not check remote unlock status: %s", resp.get("msg"))
            return True  # Assume enabled if we can't check

        for unlock_type in resp.get("result", []):
            if unlock_type.get("remote_unlock_type") == "remoteUnlockWithoutPwd":
                return unlock_type.get("open", False)

        return False

    async def async_get_device_info(self, device_id: str) -> dict | None:
        """Get device details, including category, online state, and latest status."""
        path = DEVICE_DETAILS_ENDPOINT.format(device_id=device_id)
        resp = await self._request("GET", path)

        if not resp.get("success"):
            _LOGGER.warning("Could not get device info: %s", resp.get("msg"))
            return None

        return resp.get("result")

    async def async_get_device_online(self, device_id: str) -> bool | None:
        """Get whether the device is online."""
        device_info = await self.async_get_device_info(device_id)
        if device_info is None:
            return None

        return device_info.get("online")

    async def async_get_status_map(self, device_id: str) -> dict[str, Any]:
        """Get the latest device status as a mapping from DP code to value."""
        path = STATUS_ENDPOINT.format(device_id=device_id)
        resp = await self._request("GET", path)

        if not resp.get("success"):
            _LOGGER.warning("Could not get device status: %s", resp.get("msg"))
            return {}

        return {
            dp["code"]: dp.get("value")
            for dp in resp.get("result", [])
            if "code" in dp
        }

    def is_call_active_value(self, value: Any) -> bool | None:
        """Interpret a Tuya video call/session DP value."""
        if isinstance(value, bool):
            return value

        if isinstance(value, int):
            if value == 1:
                return True
            if value == 0:
                return False
            return None

        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in TRUTHY_VALUES:
                return True
            if normalized in FALSY_VALUES:
                return False

        return None

    async def async_get_call_active(self, device_id: str) -> bool | None:
        """Get whether a doorbell/video call session appears active."""
        status = await self.async_get_status_map(device_id)
        return self.is_call_active_value(status.get("video_request_realtime"))

    async def async_get_device_specifications(self, device_id: str) -> dict | None:
        """Get device specifications for DP investigation."""
        path = SPECIFICATIONS_ENDPOINT.format(device_id=device_id)
        resp = await self._request("GET", path)

        if not resp.get("success"):
            _LOGGER.warning("Could not get device specifications: %s", resp.get("msg"))
            return None

        return resp.get("result")

    async def async_get_stream_url(
        self,
        device_id: str,
        stream_type: str = "hls",
    ) -> str | None:
        """Get a live stream URL for investigation."""
        path = STREAM_ALLOCATE_ENDPOINT.format(device_id=device_id)
        resp = await self._request("POST", path, {"type": stream_type})

        if not resp.get("success"):
            _LOGGER.warning("Could not get stream URL: %s", resp.get("msg"))
            return None

        result = resp.get("result", {})
        return result.get("url")

    async def async_get_webrtc_config(self, device_id: str) -> dict | None:
        """Get WebRTC configuration for investigation."""
        path = WEBRTC_CONFIG_ENDPOINT.format(device_id=device_id)
        resp = await self._request("GET", path)

        if not resp.get("success"):
            _LOGGER.warning("Could not get WebRTC config: %s", resp.get("msg"))
            return None

        return resp.get("result")

    async def async_get_latest_media_url(
        self,
        device_id: str,
        file_type: int = 1,
    ) -> dict | None:
        """Get latest lock media URL metadata for investigation."""
        path = LATEST_MEDIA_ENDPOINT.format(device_id=device_id)
        resp = await self._request("GET", f"{path}?file_type={file_type}")

        if not resp.get("success"):
            _LOGGER.warning("Could not get latest media URL: %s", resp.get("msg"))
            return None

        return resp.get("result")

    async def async_get_albums_media(self, device_id: str) -> dict | None:
        """Get albums media metadata for investigation."""
        path = ALBUMS_MEDIA_ENDPOINT.format(device_id=device_id)
        resp = await self._request("GET", path)

        if not resp.get("success"):
            _LOGGER.warning("Could not get albums media: %s", resp.get("msg"))
            return None

        return resp.get("result")

    async def async_get_auto_lock_time(self, device_id: str) -> int | None:
        """Get the auto-lock delay in seconds from device status."""
        status = await self.async_get_status_map(device_id)
        return status.get("auto_lock_time")

    async def async_get_battery_state(self, device_id: str) -> str | None:
        """Get the battery state from device status."""
        status = await self.async_get_status_map(device_id)
        return status.get("battery_state")

    async def async_unlock(self, device_id: str) -> bool:
        """Unlock the door via ticket flow."""
        path = TICKET_ENDPOINT.format(device_id=device_id)
        ticket_resp = await self._request("POST", path)

        if not ticket_resp.get("success"):
            _LOGGER.error("Failed to get ticket: %s", ticket_resp.get("msg"))
            return False

        ticket_id = ticket_resp["result"]["ticket_id"]

        path = DOOR_OPERATE_ENDPOINT.format(device_id=device_id)
        unlock_resp = await self._request("POST", path, {"ticket_id": ticket_id, "open": True})

        if not unlock_resp.get("success"):
            _LOGGER.error("Failed to unlock: %s", unlock_resp.get("msg"))
            return False

        _LOGGER.info("Door %s unlocked successfully", device_id)
        return True

    async def async_lock(self, device_id: str) -> bool:
        """Lock the door via ticket flow."""
        path = TICKET_ENDPOINT.format(device_id=device_id)
        ticket_resp = await self._request("POST", path)

        if not ticket_resp.get("success"):
            _LOGGER.error("Failed to get ticket: %s", ticket_resp.get("msg"))
            return False

        ticket_id = ticket_resp["result"]["ticket_id"]

        path = DOOR_OPERATE_ENDPOINT.format(device_id=device_id)
        lock_resp = await self._request("POST", path, {"ticket_id": ticket_id, "open": False})

        if not lock_resp.get("success"):
            _LOGGER.error("Failed to lock: %s", lock_resp.get("msg"))
            return False

        _LOGGER.info("Door %s locked successfully", device_id)
        return True

    async def async_get_lock_state(self, device_id: str) -> bool | None:
        """Get lock_motor_state. Returns True if unlocked, False if locked, None on error."""
        path = STATUS_ENDPOINT.format(device_id=device_id)
        resp = await self._request("GET", path)

        if not resp.get("success"):
            _LOGGER.error("Failed to get status: %s", resp.get("msg"))
            return None

        for dp in resp.get("result", []):
            if dp["code"] == "lock_motor_state":
                return dp["value"]

        return None
