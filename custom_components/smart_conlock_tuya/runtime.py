"""Runtime state for Smart (Con)lock tuya entries."""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
import json
import logging
import time
from typing import Any
from urllib.parse import urlsplit
import uuid

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later

from .tuya_api import JTMSPRO_REQUEST_WINDOW, TuyaCloudApi

_LOGGER = logging.getLogger(__name__)

REQUEST_EVENT_CODES = {
    "doorbell",
    "initiative_message",
    "video_request_realtime",
    "photo_again",
}
LOCK_STATE_CODES = {"lock_motor_state"}
ONLINE_CODES = {"online", "device_online", "net_state", "connectivity"}
GCM_TAG_LENGTH = 16


class DeviceStatusNotificationClient:
    """Tuya Device Status Notification MQTT client for custom cloud auth."""

    def __init__(
        self,
        api: TuyaCloudApi,
        uid: str,
        message_callback: Callable[[dict[str, Any]], None],
        diagnostic_callback: Callable[[dict[str, Any]], None],
    ) -> None:
        self._api = api
        self._uid = uid
        self._message_callback = message_callback
        self._diagnostic_callback = diagnostic_callback
        self._client: Any = None
        self._password = ""
        self._source_topic: Any = {}
        self._expire_time = 0
        self._link_id = f"smart-conlock-tuya.{uuid.uuid1()}"

    @property
    def expire_time(self) -> int:
        """Return MQTT config expiry in seconds."""
        return self._expire_time

    async def async_start(self, hass: HomeAssistant) -> None:
        """Fetch MQTT config and connect."""
        config = await self._api.async_get_open_hub_access_config(
            self._uid,
            self._link_id,
        )
        if config is None:
            raise ConnectionError("Open Hub access config unavailable")

        topics = _iter_topics(config.get("source_topic", {}))
        self._emit_diagnostic(
            "access_config",
            topic_count=len(topics),
            subscribed_topic_count=0,
        )
        _LOGGER.debug(
            "Tuya Device Status Notification config ready: topic_count=%s expire_time=%s",
            len(topics),
            config.get("expire_time"),
        )

        await hass.async_add_executor_job(self._start, config)

    async def async_stop(self, hass: HomeAssistant) -> None:
        """Disconnect the MQTT client."""
        await hass.async_add_executor_job(self._stop)

    def _start(self, config: dict[str, Any]) -> None:
        """Start paho-mqtt from a worker thread."""
        from paho.mqtt import client as mqtt  # type: ignore

        self._password = config.get("password", "")
        self._source_topic = config.get("source_topic", {})
        self._expire_time = int(config.get("expire_time") or 0)

        client_id = config.get("client_id", "")
        try:
            mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id)
        except AttributeError:
            mqttc = mqtt.Client(client_id)
        mqttc.username_pw_set(config.get("username", ""), self._password)
        mqttc.on_connect = self._on_connect
        mqttc.on_message = self._on_message
        mqttc.on_disconnect = self._on_disconnect

        url = urlsplit(config.get("url", ""))
        if url.scheme == "ssl":
            mqttc.tls_set()
        if not url.hostname or not url.port:
            raise ConnectionError(f"Invalid MQTT URL: {config.get('url')}")

        _LOGGER.debug("Connecting Tuya Device Status Notification MQTT")
        mqttc.connect(url.hostname, url.port)
        mqttc.loop_start()
        self._client = mqttc

    def _stop(self) -> None:
        """Stop paho-mqtt."""
        if self._client is None:
            return
        self._client.loop_stop()
        self._client.disconnect()
        self._client = None

    def _on_connect(self, mqttc: Any, _userdata: Any, _flags: Any, rc: int) -> None:
        """Subscribe to device topics after connecting."""
        topics = _iter_topics(self._source_topic)
        self._emit_diagnostic(
            "connect",
            connect_result=rc,
            topic_count=len(topics),
        )
        _LOGGER.debug(
            "Tuya Device Status Notification MQTT connected: rc=%s topic_count=%s",
            rc,
            len(topics),
        )
        if rc != 0:
            _LOGGER.warning("Tuya Device Status Notification connect failed: %s", rc)
            return

        if not topics:
            self._emit_diagnostic(
                "subscribe",
                subscribed_topic_count=0,
                last_subscribe_status="no_topics",
            )
            _LOGGER.debug("Tuya Device Status Notification has no topics to subscribe")
            return

        subscribed = 0
        last_status = "unknown"
        for topic in topics:
            result, _mid = mqttc.subscribe(topic)
            if result == 0:
                subscribed += 1
                last_status = "ok"
            else:
                last_status = f"result_{result}"

        self._emit_diagnostic(
            "subscribe",
            subscribed_topic_count=subscribed,
            last_subscribe_status=last_status,
        )
        _LOGGER.debug(
            "Tuya Device Status Notification subscribed: subscribed_topic_count=%s topic_count=%s last_status=%s",
            subscribed,
            len(topics),
            last_status,
        )

    def _on_disconnect(self, _mqttc: Any, _userdata: Any, rc: int) -> None:
        """Log unexpected disconnects."""
        if rc != 0:
            _LOGGER.warning("Tuya Device Status Notification disconnected: %s", rc)

    def _on_message(self, _mqttc: Any, _userdata: Any, msg: Any) -> None:
        """Decode and forward MQTT messages."""
        try:
            msg_dict = json.loads(msg.payload.decode("utf8"))
            self._emit_diagnostic(
                "message",
                wrapper_keys=_safe_keys(msg_dict),
            )
            _LOGGER.debug(
                "Tuya Device Status Notification message received: wrapper_keys=%s",
                _safe_keys(msg_dict),
            )
            event_time = msg_dict.get("t", "")
            decoded_data = self._decode_message(
                msg_dict["data"],
                self._password,
                event_time,
            )
            msg_dict["data"] = decoded_data
            self._emit_diagnostic(
                "decoded",
                decoded_payload_type=type(decoded_data).__name__,
                decoded_payload_keys=_safe_keys(decoded_data),
                decode_error=None,
            )
            _LOGGER.debug(
                "Tuya Device Status Notification payload decoded: type=%s keys=%s",
                type(decoded_data).__name__,
                _safe_keys(decoded_data),
            )
            self._message_callback(msg_dict)
        except Exception as err:  # noqa: BLE001
            self._emit_diagnostic("decode_error", decode_error=str(err))
            _LOGGER.warning("Could not decode Tuya notification message: %s", err)

    def _emit_diagnostic(self, event: str, **data: Any) -> None:
        """Emit sanitized diagnostics from the MQTT worker thread."""
        payload = {"event": event, **data}
        self._diagnostic_callback(payload)

    def _decode_message(self, b64msg: str, password: str, event_time: Any) -> Any:
        """Decode Tuya custom Open Hub AES-GCM payloads."""
        from Crypto.Cipher import AES  # type: ignore

        key = password[8:24]
        buffer = base64.b64decode(b64msg)
        iv_length = int.from_bytes(buffer[0:4], byteorder="big")
        iv_buffer = buffer[4 : iv_length + 4]
        data_buffer = buffer[iv_length + 4 : len(buffer) - GCM_TAG_LENGTH]
        tag_buffer = buffer[len(buffer) - GCM_TAG_LENGTH :]
        aad_buffer = str(event_time).encode("utf8")

        cipher = AES.new(key.encode("utf8"), AES.MODE_GCM, nonce=iv_buffer)
        cipher.update(aad_buffer)
        plaintext = cipher.decrypt_and_verify(data_buffer, tag_buffer).decode("utf8")
        return json.loads(plaintext)


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
    fallback_last_refresh_time: int | None = None
    fallback_burst_active: bool = False
    fallback_burst_remaining: int = 0
    request_fast_fallback_active: bool = False
    request_fast_fallback_reason: str | None = None
    request_fast_fallback_interval: int | None = None
    request_last_refresh_time: int | None = None
    request_window_seconds: int = JTMSPRO_REQUEST_WINDOW
    source: str | None = None
    last_event_time: int | None = None
    last_event_code: str | None = None
    last_event_value: Any = None
    doorbell: Any = None
    video_request_realtime: Any = None
    photo_again: Any = None
    initiative_message_decoded: dict[str, Any] | None = None
    locked: bool | None = None
    lock_motor_state: Any = None
    state_source: str | None = None
    state_confidence: str = "unknown"
    lock_report_log_error: str | None = None
    lock_report_log_count: int | None = None
    last_lock_operation: dict[str, Any] | None = None
    push_connect_result: int | None = None
    push_topic_count: int | None = None
    push_subscribed_topic_count: int | None = None
    push_last_subscribe_status: str | None = None
    push_message_count: int = 0
    push_last_message_time: int | None = None
    push_last_wrapper_keys: list[str] | None = None
    push_last_decoded_payload_type: str | None = None
    push_last_decoded_payload_keys: list[str] | None = None
    push_last_decode_error: str | None = None
    push_last_parsed_event_count: int | None = None
    push_last_event_codes: list[str] | None = None
    push_last_ignored_device_id: str | None = None

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
            "fallback_last_refresh_time": self.fallback_last_refresh_time,
            "fallback_burst_active": self.fallback_burst_active,
            "fallback_burst_remaining": self.fallback_burst_remaining,
            "request_fast_fallback_active": self.request_fast_fallback_active,
            "request_fast_fallback_reason": self.request_fast_fallback_reason,
            "request_fast_fallback_interval": self.request_fast_fallback_interval,
            "request_last_refresh_time": self.request_last_refresh_time,
            "request_window_seconds": self.request_window_seconds,
            "source": self.source,
            "last_event_time": self.last_event_time,
            "seconds_since_event": seconds_since_event,
            "request_expires_in": expires_in,
            "doorbell": self.doorbell,
            "video_request_realtime": self.video_request_realtime,
            "photo_again": self.photo_again,
            "initiative_message_decoded": self.initiative_message_decoded,
            "last_event_code": self.last_event_code,
            "last_event_value": self.last_event_value,
            "push_connect_result": self.push_connect_result,
            "push_topic_count": self.push_topic_count,
            "push_subscribed_topic_count": self.push_subscribed_topic_count,
            "push_last_subscribe_status": self.push_last_subscribe_status,
            "push_message_count": self.push_message_count,
            "push_last_message_time": self.push_last_message_time,
            "push_last_wrapper_keys": self.push_last_wrapper_keys,
            "push_last_decoded_payload_type": self.push_last_decoded_payload_type,
            "push_last_decoded_payload_keys": self.push_last_decoded_payload_keys,
            "push_last_decode_error": self.push_last_decode_error,
            "push_last_parsed_event_count": self.push_last_parsed_event_count,
            "push_last_event_codes": self.push_last_event_codes,
            "push_last_ignored_device_id": self.push_last_ignored_device_id,
        }

    def lock_state(self) -> dict[str, Any]:
        """Return a Home Assistant attribute friendly lock state."""
        return {
            "locked": self.locked,
            "lock_motor_state": self.lock_motor_state,
            "state_source": self.state_source,
            "state_confidence": self.state_confidence,
            "lock_report_log_error": self.lock_report_log_error,
            "lock_report_log_count": self.lock_report_log_count,
            "last_lock_operation": self.last_lock_operation,
        }


class SmartConlockRuntime:
    """Coordinate push state for a configured jtmspro lock."""

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
        self._unsub_expiry: CALLBACK_TYPE | None = None
        self._unsub_mq_refresh: CALLBACK_TYPE | None = None
        self._mq: DeviceStatusNotificationClient | None = None

    async def async_start(self) -> None:
        """Start runtime services."""
        await self._async_start_mq()

    async def async_stop(self) -> None:
        """Stop runtime services."""
        if self._unsub_expiry:
            self._unsub_expiry()
            self._unsub_expiry = None
        if self._unsub_mq_refresh:
            self._unsub_mq_refresh()
            self._unsub_mq_refresh = None
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

    async def async_handle_message(self, message: Any) -> None:
        """Handle a Tuya Device Status Notification message."""
        changed = False
        events = iter_device_status_events(message)
        self.state.push_last_parsed_event_count = len(events)
        self.state.push_last_event_codes = [
            event["code"] for event in events if event.get("code") is not None
        ]
        changed = True
        _LOGGER.debug(
            "Parsed Tuya Device Status Notification events: count=%s codes=%s",
            len(events),
            self.state.push_last_event_codes,
        )

        for event in events:
            if event.get("device_id") and event["device_id"] != self.device_id:
                self.state.push_last_ignored_device_id = event["device_id"]
                _LOGGER.debug(
                    "Ignoring Tuya notification for different device_id: %s",
                    event["device_id"],
                )
                continue

            code = event.get("code")
            value = event.get("value")
            event_time = event.get("event_time")
            has_event_time = event_time not in (None, "", 0, "0")

            if code in ONLINE_CODES:
                online = self.api.is_call_active_value(value)
                if online != self.state.online and online is not None:
                    self.state.online = online
                    changed = True
                continue

            if self.state.online is not True:
                self.state.online = True
                changed = True

            if code in LOCK_STATE_CODES:
                locked = self.api.interpret_lock_motor_state(value)
                if locked is not None:
                    self.state.locked = locked
                    self.state.lock_motor_state = value
                    self.state.state_source = "push_lock_motor_state"
                    self.state.state_confidence = "physical_dp"
                    self.state.last_lock_operation = {
                        "action": "locked" if locked else "unlocked",
                        "source": "push",
                        "event_time": self.api._event_time_ms(event_time)
                        if has_event_time
                        else int(time.time() * 1000),
                        "value": value,
                    }
                    changed = True
                continue

            if code not in REQUEST_EVENT_CODES:
                _LOGGER.debug("Ignoring unsupported Tuya notification code: %s", code)
                continue

            self.state.last_event_code = code
            self.state.last_event_value = value
            if has_event_time:
                self.state.last_event_time = self.api._event_time_ms(event_time)

            if code == "doorbell":
                self.state.doorbell = value
                if has_event_time and self.api._is_active_doorbell_value(value):
                    self._activate_request(code, value, event_time, notify=False)
                    _LOGGER.debug("Activated request window from doorbell push event")
                    changed = True
            elif code == "initiative_message":
                active, decoded = self.api._is_active_initiative_message(value)
                self.state.initiative_message_decoded = decoded
                if has_event_time and active:
                    self._activate_request(code, value, event_time, notify=False)
                    _LOGGER.debug(
                        "Activated request window from initiative_message push event"
                    )
                    changed = True
            elif code == "video_request_realtime":
                self.state.video_request_realtime = value
                self.state.diagnostic_status = (
                    "video_request_realtime_observed"
                    if not self.state.request_active
                    else self.state.diagnostic_status
                )
                changed = True
            elif code == "photo_again":
                self.state.photo_again = value
                self.state.diagnostic_status = (
                    "photo_again_observed"
                    if not self.state.request_active
                    else self.state.diagnostic_status
                )
                changed = True

            if not has_event_time and code in {"doorbell", "initiative_message"}:
                self.state.diagnostic_status = "untimed_event_ignored"
                _LOGGER.debug(
                    "Ignoring %s push event because it has no usable event_time",
                    code,
                )
                changed = True

        if changed:
            self.async_notify_listeners()

    async def async_handle_push_diagnostic(self, diagnostic: dict[str, Any]) -> None:
        """Merge sanitized MQTT diagnostics into runtime state."""
        event = diagnostic.get("event")

        if "connect_result" in diagnostic:
            self.state.push_connect_result = diagnostic.get("connect_result")
        if "topic_count" in diagnostic:
            self.state.push_topic_count = diagnostic.get("topic_count")
        if "subscribed_topic_count" in diagnostic:
            self.state.push_subscribed_topic_count = diagnostic.get(
                "subscribed_topic_count"
            )
        if "last_subscribe_status" in diagnostic:
            self.state.push_last_subscribe_status = diagnostic.get(
                "last_subscribe_status"
            )

        if event == "message":
            self.state.push_message_count += 1
            self.state.push_last_message_time = int(time.time() * 1000)
            self.state.push_last_wrapper_keys = diagnostic.get("wrapper_keys")
        elif event == "decoded":
            self.state.push_last_decoded_payload_type = diagnostic.get(
                "decoded_payload_type"
            )
            self.state.push_last_decoded_payload_keys = diagnostic.get(
                "decoded_payload_keys"
            )
            self.state.push_last_decode_error = diagnostic.get("decode_error")
        elif event == "decode_error":
            self.state.push_last_decode_error = diagnostic.get("decode_error")

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
        event_time_ms = (
            self.api._event_time_ms(event_time)
            if event_time
            else int(time.time() * 1000)
        )
        expires_at = (event_time_ms / 1000) + JTMSPRO_REQUEST_WINDOW
        if expires_at <= time.time():
            return

        if self._unsub_expiry:
            self._unsub_expiry()
            self._unsub_expiry = None

        self.state.request_active = True
        self.state.request_expires_at = expires_at
        self.state.diagnostic_status = diagnostic_status
        self.state.last_error = None
        self.state.source = source
        self.state.last_event_code = source
        self.state.last_event_value = value
        self.state.last_event_time = event_time_ms
        self._unsub_expiry = async_call_later(
            self.hass,
            max(0, expires_at - time.time()),
            self._expire_request,
        )
        if notify:
            self.async_notify_listeners()

    @callback
    def record_home_assistant_operation(
        self,
        action: str,
        locked: bool,
        source: str = "home_assistant",
    ) -> None:
        """Record a local lock state change immediately."""
        self.state.locked = locked
        self.state.state_source = source
        self.state.state_confidence = "command_assumed"
        self.state.last_lock_operation = {
            "action": action,
            "source": source,
            "event_time": int(time.time() * 1000),
            "value": None,
        }
        self.async_notify_listeners()

    @callback
    def _expire_request(self, _now: Any = None) -> None:
        """Close an active unlock request window."""
        self._unsub_expiry = None
        self.state.request_active = False
        self.state.request_expires_at = None
        self.state.fallback_burst_active = False
        self.state.fallback_burst_remaining = 0
        self.state.diagnostic_status = "request_window_expired"
        self.async_notify_listeners()

    async def _async_start_mq(self) -> None:
        """Start Tuya Device Status Notification MQTT client."""
        try:
            uid = await self._async_resolve_uid()
            if not uid:
                raise ConnectionError("Tuya uid unavailable")

            self._mq = DeviceStatusNotificationClient(
                self.api,
                uid,
                self._handle_mq_message_threadsafe,
                self._handle_mq_diagnostic_threadsafe,
            )
            await self._mq.async_start(self.hass)
            self.state.diagnostic_status = "push_connected"
            self.state.last_error = None
            self._schedule_mq_refresh()
            self.async_notify_listeners()
        except Exception as err:  # noqa: BLE001
            self._mq = None
            self.state.last_error = f"push_start_failed: {err}"
            self.state.diagnostic_status = "push_unavailable"
            _LOGGER.warning("Could not start Tuya Device Status Notification: %s", err)
            self.async_notify_listeners()

    async def _async_stop_mq(self) -> None:
        """Stop Tuya Device Status Notification MQTT client."""
        mq = self._mq
        if mq is None:
            return

        try:
            await mq.async_stop(self.hass)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not stop Tuya Device Status Notification: %s", err)
        finally:
            self._mq = None

    def _handle_mq_message_threadsafe(self, message: Any) -> None:
        """Bridge MQTT callbacks into the Home Assistant event loop."""
        self.hass.loop.call_soon_threadsafe(
            lambda: self.hass.async_create_task(self.async_handle_message(message))
        )

    def _handle_mq_diagnostic_threadsafe(self, diagnostic: dict[str, Any]) -> None:
        """Bridge MQTT diagnostics into the Home Assistant event loop."""
        self.hass.loop.call_soon_threadsafe(
            lambda: self.hass.async_create_task(
                self.async_handle_push_diagnostic(diagnostic)
            )
        )

    async def _async_resolve_uid(self) -> str | None:
        """Resolve the Tuya uid needed by Open Hub access config."""
        if self.api.uid:
            return self.api.uid

        device_info = await self.api.async_get_device_info(self.device_id)
        if not device_info:
            return None
        uid = device_info.get("uid")
        return uid if isinstance(uid, str) and uid else None

    @callback
    def _schedule_mq_refresh(self) -> None:
        """Refresh MQTT access config before it expires."""
        if self._unsub_mq_refresh:
            self._unsub_mq_refresh()
            self._unsub_mq_refresh = None
        if self._mq is None or self._mq.expire_time <= 60:
            return

        self._unsub_mq_refresh = async_call_later(
            self.hass,
            self._mq.expire_time - 60,
            self._restart_mq,
        )

    @callback
    def _restart_mq(self, _now: Any = None) -> None:
        """Restart MQTT after access config expires."""
        self._unsub_mq_refresh = None
        self.hass.async_create_task(self._async_restart_mq())

    async def _async_restart_mq(self) -> None:
        """Restart MQTT client."""
        await self._async_stop_mq()
        await self._async_start_mq()


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
    elif isinstance(message, list):
        payloads = message
    else:
        payload = getattr(message, "payload", None) or getattr(message, "data", None)
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = None
        payloads = [payload] if isinstance(payload, (dict, list)) else []

    flattened: list[dict[str, Any]] = []
    for payload in payloads:
        if isinstance(payload, dict):
            flattened.append(payload)
        elif isinstance(payload, list):
            flattened.extend(_flatten_payloads(payload))
        elif isinstance(payload, str):
            flattened.extend(_flatten_payloads(payload))

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


def _iter_topics(source_topic: Any) -> list[str]:
    """Return MQTT topics from common Tuya config shapes."""
    if isinstance(source_topic, dict):
        topics: list[str] = []
        for value in source_topic.values():
            topics.extend(_iter_topics(value))
        return topics
    if isinstance(source_topic, list):
        topics = []
        for value in source_topic:
            topics.extend(_iter_topics(value))
        return topics
    if isinstance(source_topic, str):
        return [source_topic]
    return []


def _safe_keys(value: Any) -> list[str] | None:
    """Return sanitized payload keys for diagnostics."""
    if isinstance(value, dict):
        return sorted(str(key) for key in value.keys())
    if isinstance(value, list):
        keys: set[str] = set()
        for item in value:
            if isinstance(item, dict):
                keys.update(str(key) for key in item.keys())
        return sorted(keys) if keys else None
    return None
