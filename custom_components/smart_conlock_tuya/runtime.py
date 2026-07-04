"""Runtime state for Smart (Con)lock tuya entries."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
import inspect
import json
import logging
import time
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from .const import CONF_ACCESS_ID, CONF_ACCESS_SECRET, CONF_API_REGION
from .tuya_api import JTMSPRO_REQUEST_WINDOW, TuyaCloudApi

_LOGGER = logging.getLogger(__name__)

FALLBACK_INTERVAL = timedelta(seconds=60)
REQUEST_EVENT_CODES = {"doorbell", "initiative_message", "video_request_realtime"}
ONLINE_CODES = {"online", "device_online", "net_state", "connectivity"}
MQ_ENDPOINTS = {
    "eu": "https://openapi.tuyaeu.com",
    "us": "https://openapi.tuyaus.com",
    "cn": "https://openapi.tuyacn.com",
    "in": "https://openapi.tuyain.com",
}


@dataclass
class SmartConlockRuntimeState:
    """Shared runtime state for a configured lock."""

    online: bool | None = None
    request_active: bool = False
    request_expires_at: float | None = None
    diagnostic_status: str = "starting"
    last_error: str | None = None
    report_log_error: str | None = None
    report_log_count: int | None = None
    request_window_seconds: int = JTMSPRO_REQUEST_WINDOW
    source: str | None = None
    last_event_time: int | None = None
    last_event_code: str | None = None
    last_event_value: Any = None
    doorbell: Any = None
    video_request_realtime: Any = None
    initiative_message_decoded: dict[str, Any] | None = None

    def request_state(self) -> dict[str, Any]:
        """Return a Home Assistant attribute friendly request state."""
        seconds_since_event = None
        if self.last_event_time:
            now_ms = int(time.time() * 1000)
            seconds_since_event = max(0, (now_ms - self.last_event_time) // 1000)

        expires_in = None
        if self.request_expires_at is not None:
            expires_in = max(0, int(self.request_expires_at - time.time()))

        return {
            "active": self.request_active,
            "diagnostic_status": self.diagnostic_status,
            "last_error": self.last_error,
            "report_log_error": self.report_log_error,
            "report_log_count": self.report_log_count,
            "request_window_seconds": self.request_window_seconds,
            "source": self.source,
            "last_event_time": self.last_event_time,
            "seconds_since_event": seconds_since_event,
            "request_expires_in": expires_in,
            "doorbell": self.doorbell,
            "video_request_realtime": self.video_request_realtime,
            "initiative_message_decoded": self.initiative_message_decoded,
            "last_event_code": self.last_event_code,
            "last_event_value": self.last_event_value,
        }


class SmartConlockRuntime:
    """Coordinate push and fallback state for a configured jtmspro lock."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: TuyaCloudApi,
        entry_data: dict[str, Any],
        device_id: str,
    ) -> None:
        self.hass = hass
        self.api = api
        self.entry_data = entry_data
        self.device_id = device_id
        self.state = SmartConlockRuntimeState()
        self._listeners: list[Callable[[], None]] = []
        self._unsub_fallback: CALLBACK_TYPE | None = None
        self._unsub_expiry: CALLBACK_TYPE | None = None
        self._mq: Any = None
        self._mq_listener: Callable[[Any], None] | None = None

    async def async_start(self) -> None:
        """Start runtime services."""
        await self.async_refresh_fallback()
        await self._async_start_mq()
        self._unsub_fallback = async_track_time_interval(
            self.hass,
            self._async_refresh_fallback_callback,
            FALLBACK_INTERVAL,
        )

    async def async_stop(self) -> None:
        """Stop runtime services."""
        if self._unsub_fallback:
            self._unsub_fallback()
            self._unsub_fallback = None
        if self._unsub_expiry:
            self._unsub_expiry()
            self._unsub_expiry = None
        await self._async_stop_mq()
        self._listeners.clear()

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Register a state-change listener."""
        self._listeners.append(listener)

        @callback
        def _remove_listener() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove_listener

    @callback
    def async_notify_listeners(self) -> None:
        """Notify entities that runtime state changed."""
        for listener in list(self._listeners):
            listener()

    async def async_refresh_fallback(self, _now: Any = None) -> None:
        """Refresh slow fallback state from REST APIs."""
        changed = False
        try:
            online = await self.api.async_get_device_online(self.device_id)
            if online != self.state.online:
                self.state.online = online
                changed = True
        except Exception as err:  # noqa: BLE001
            self.state.last_error = f"online_refresh_failed: {err}"
            self.state.diagnostic_status = "fallback_error"
            changed = True

        try:
            request_state = await self.api.async_get_jtmspro_request_state(
                self.device_id
            )
            changed = self._merge_fallback_request_state(request_state) or changed
        except Exception as err:  # noqa: BLE001
            self.state.last_error = f"report_log_refresh_failed: {err}"
            self.state.diagnostic_status = "fallback_error"
            changed = True

        if changed:
            self.async_notify_listeners()

    async def _async_refresh_fallback_callback(self, now: Any) -> None:
        await self.async_refresh_fallback(now)

    def _merge_fallback_request_state(self, request_state: dict[str, Any]) -> bool:
        """Merge slow report-log fallback state into runtime state."""
        before = self.state.request_state()

        self.state.report_log_error = request_state.get("report_log_error")
        self.state.report_log_count = request_state.get("report_log_count")
        self.state.request_window_seconds = request_state.get(
            "request_window_seconds", JTMSPRO_REQUEST_WINDOW
        )

        if request_state.get("doorbell") is not None:
            self.state.doorbell = request_state.get("doorbell")
        if request_state.get("video_request_realtime") is not None:
            self.state.video_request_realtime = request_state.get(
                "video_request_realtime"
            )
        if request_state.get("initiative_message_decoded") is not None:
            self.state.initiative_message_decoded = request_state.get(
                "initiative_message_decoded"
            )

        if request_state.get("active") is True:
            event_time = request_state.get("last_event_time")
            self._activate_request(
                source=request_state.get("source") or "report_logs",
                value=None,
                event_time=event_time,
                diagnostic_status="fallback_recent_request_detected",
                notify=False,
            )
        elif not self.state.request_active:
            self.state.diagnostic_status = request_state.get(
                "diagnostic_status", "fallback_no_recent_request"
            )

        return before != self.state.request_state()

    async def async_handle_message(self, message: Any) -> None:
        """Handle a Tuya Device Status Notification message."""
        changed = False
        for event in iter_device_status_events(message):
            if event.get("device_id") and event["device_id"] != self.device_id:
                continue

            code = event.get("code")
            value = event.get("value")
            event_time = event.get("event_time")

            if code in ONLINE_CODES:
                online = self.api.is_call_active_value(value)
                if online != self.state.online and online is not None:
                    self.state.online = online
                    changed = True
                continue

            if code not in REQUEST_EVENT_CODES:
                continue

            self.state.last_event_code = code
            self.state.last_event_value = value
            if event_time:
                self.state.last_event_time = self.api._event_time_ms(event_time)

            if code == "doorbell":
                self.state.doorbell = value
                if self.api._is_active_doorbell_value(value):
                    self._activate_request(code, value, event_time, notify=False)
                    changed = True
            elif code == "initiative_message":
                active, decoded = self.api._is_active_initiative_message(value)
                self.state.initiative_message_decoded = decoded
                if active:
                    self._activate_request(code, value, event_time, notify=False)
                    changed = True
            elif code == "video_request_realtime":
                self.state.video_request_realtime = value
                self.state.diagnostic_status = (
                    "video_request_realtime_observed"
                    if not self.state.request_active
                    else self.state.diagnostic_status
                )
                changed = True

        if changed:
            self.async_notify_listeners()

    @callback
    def _activate_request(
        self,
        source: str,
        value: Any,
        event_time: Any,
        diagnostic_status: str = "push_recent_request_detected",
        notify: bool = True,
    ) -> None:
        """Mark the unlock request window active."""
        if self._unsub_expiry:
            self._unsub_expiry()
            self._unsub_expiry = None

        self.state.request_active = True
        self.state.request_expires_at = time.time() + JTMSPRO_REQUEST_WINDOW
        self.state.diagnostic_status = diagnostic_status
        self.state.last_error = None
        self.state.source = source
        self.state.last_event_code = source
        self.state.last_event_value = value
        self.state.last_event_time = (
            self.api._event_time_ms(event_time)
            if event_time
            else int(time.time() * 1000)
        )
        self._unsub_expiry = async_call_later(
            self.hass,
            JTMSPRO_REQUEST_WINDOW,
            self._expire_request,
        )
        if notify:
            self.async_notify_listeners()

    @callback
    def _expire_request(self, _now: Any = None) -> None:
        """Close an active unlock request window."""
        self._unsub_expiry = None
        self.state.request_active = False
        self.state.request_expires_at = None
        self.state.diagnostic_status = "request_window_expired"
        self.async_notify_listeners()

    async def _async_start_mq(self) -> None:
        """Start Tuya OpenMQ listener if the SDK is available."""
        try:
            from tuya_iot import AuthType, TuyaOpenAPI, TuyaOpenMQ  # type: ignore
        except ImportError as err:
            try:
                from tuya_iot import TuyaOpenAPI, TuyaOpenMQ  # type: ignore

                AuthType = None
            except ImportError:
                self.state.last_error = f"tuya_iot_import_failed: {err}"
                self.state.diagnostic_status = "push_unavailable"
                return

        endpoint = MQ_ENDPOINTS.get(
            self.entry_data.get(CONF_API_REGION),
            self.api.base_url,
        )
        access_id = self.entry_data[CONF_ACCESS_ID]
        access_secret = self.entry_data[CONF_ACCESS_SECRET]

        try:
            auth_type = getattr(AuthType, "CUSTOM", None) if AuthType else None
            if auth_type is not None:
                openapi = TuyaOpenAPI(
                    endpoint,
                    access_id,
                    access_secret,
                    auth_type,
                )
            else:
                openapi = TuyaOpenAPI(endpoint, access_id, access_secret)
            connect = getattr(openapi, "connect", None)
            if callable(connect):
                await self._async_run_sdk_call(connect)

            self._mq = TuyaOpenMQ(openapi)
            self._mq_listener = self._handle_mq_message_threadsafe
            self._mq.add_message_listener(self._mq_listener)
            await self._async_run_sdk_call(self._mq.start)
            self.state.diagnostic_status = "push_connected"
            self.state.last_error = None
            self.async_notify_listeners()
        except Exception as err:  # noqa: BLE001
            self._mq = None
            self._mq_listener = None
            self.state.last_error = f"push_start_failed: {err}"
            self.state.diagnostic_status = "push_unavailable"
            _LOGGER.warning("Could not start Tuya Device Status Notification: %s", err)
            self.async_notify_listeners()

    async def _async_stop_mq(self) -> None:
        """Stop Tuya OpenMQ listener."""
        mq = self._mq
        if mq is None:
            return

        try:
            if self._mq_listener is not None:
                remove_listener = getattr(mq, "remove_message_listener", None)
                if callable(remove_listener):
                    await self._async_run_sdk_call(remove_listener, self._mq_listener)
            stop = getattr(mq, "stop", None)
            if callable(stop):
                await self._async_run_sdk_call(stop)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not stop Tuya Device Status Notification: %s", err)
        finally:
            self._mq = None
            self._mq_listener = None

    def _handle_mq_message_threadsafe(self, message: Any) -> None:
        """Bridge OpenMQ callbacks into the Home Assistant event loop."""
        self.hass.loop.call_soon_threadsafe(
            lambda: self.hass.async_create_task(self.async_handle_message(message))
        )

    async def _async_run_sdk_call(self, func: Callable, *args: Any) -> Any:
        """Run a Tuya SDK call without blocking the Home Assistant event loop."""
        if inspect.iscoroutinefunction(func):
            return await func(*args)
        result = await self.hass.async_add_executor_job(func, *args)
        if inspect.isawaitable(result):
            return await result
        return result


def iter_device_status_events(message: Any) -> list[dict[str, Any]]:
    """Extract device status events from common Tuya message shapes."""
    payloads = _flatten_payloads(message)
    events: list[dict[str, Any]] = []

    for payload in payloads:
        device_id = _first_present(
            payload,
            "devId",
            "dev_id",
            "device_id",
            "deviceId",
            "id",
        )
        event_time = _first_present(payload, "t", "time", "event_time", "eventTime")

        code = _first_present(payload, "code", "dpCode", "dp_code")
        if code is not None and _event_value(payload) is not None:
            events.append(
                {
                    "device_id": device_id,
                    "code": code,
                    "value": _event_value(payload),
                    "event_time": event_time,
                }
            )

        status = payload.get("status")
        if isinstance(status, list):
            for item in status:
                if not isinstance(item, dict):
                    continue
                item_code = _first_present(item, "code", "dpCode", "dp_code")
                if item_code is None:
                    continue
                events.append(
                    {
                        "device_id": _first_present(
                            item,
                            "devId",
                            "dev_id",
                            "device_id",
                            "deviceId",
                        )
                        or device_id,
                        "code": item_code,
                        "value": _event_value(item),
                        "event_time": _first_present(
                            item, "t", "time", "event_time", "eventTime"
                        )
                        or event_time,
                    }
                )
        elif isinstance(status, dict):
            item_code = _first_present(status, "code", "dpCode", "dp_code")
            if item_code is not None:
                events.append(
                    {
                        "device_id": _first_present(
                            status,
                            "devId",
                            "dev_id",
                            "device_id",
                            "deviceId",
                        )
                        or device_id,
                        "code": item_code,
                        "value": _event_value(status),
                        "event_time": _first_present(
                            status, "t", "time", "event_time", "eventTime"
                        )
                        or event_time,
                    }
                )
            else:
                for item_code, item_value in status.items():
                    events.append(
                        {
                            "device_id": device_id,
                            "code": item_code,
                            "value": item_value,
                            "event_time": event_time,
                        }
                    )

        for nested_key in ("data", "payload"):
            nested = payload.get(nested_key)
            if isinstance(nested, dict):
                for event in iter_device_status_events(nested):
                    event["device_id"] = event.get("device_id") or device_id
                    event["event_time"] = event.get("event_time") or event_time
                    events.append(event)
            elif isinstance(nested, list):
                for item in nested:
                    if not isinstance(item, dict):
                        continue
                    for event in iter_device_status_events(item):
                        event["device_id"] = event.get("device_id") or device_id
                        event["event_time"] = event.get("event_time") or event_time
                        events.append(event)
            elif isinstance(nested, str):
                for event in iter_device_status_events(nested):
                    event["device_id"] = event.get("device_id") or device_id
                    event["event_time"] = event.get("event_time") or event_time
                    events.append(event)

    return events


def _flatten_payloads(message: Any) -> list[dict[str, Any]]:
    """Return dict payloads from Tuya SDK message wrappers."""
    if isinstance(message, str):
        try:
            message = json.loads(message)
        except json.JSONDecodeError:
            return []

    if isinstance(message, dict):
        payloads = [message]
    else:
        payload = getattr(message, "payload", None) or getattr(message, "data", None)
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = None
        payloads = [payload] if isinstance(payload, dict) else []

    flattened: list[dict[str, Any]] = []
    for payload in payloads:
        flattened.append(payload)

    return flattened


def _first_present(payload: dict[str, Any], *keys: str) -> Any:
    """Return the first present key value."""
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _event_value(payload: dict[str, Any]) -> Any:
    """Return a status value from common Tuya keys."""
    return _first_present(payload, "value", "dpValue", "dp_value")
