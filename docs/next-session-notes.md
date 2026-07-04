# Next Session Notes

Date: 2026-07-04

## Current Goal

This fork is now aimed at the user's `jtmspro` Conlock/video smart lock and is named:

- Integration name: `Smart (Con)lock tuya`
- Domain/folder: `smart_conlock_tuya`
- Repository: `batty211/smartlock-tuya`

The integration should expose a Tuya lock, battery sensor, online state, and a request-aware unlock flow for a `jtmspro` video smart lock.

## Current Repo State

The code has already been renamed from `tuya_smart_lock` to:

```text
custom_components/smart_conlock_tuya
```

Important files:

- `custom_components/smart_conlock_tuya/manifest.json`
- `custom_components/smart_conlock_tuya/const.py`
- `custom_components/smart_conlock_tuya/tuya_api.py`
- `custom_components/smart_conlock_tuya/runtime.py`
- `custom_components/smart_conlock_tuya/lock.py`
- `custom_components/smart_conlock_tuya/binary_sensor.py`
- `custom_components/smart_conlock_tuya/sensor.py`
- `README.md`

Latest validation passed:

```bash
env PYTHONPYCACHEPREFIX=/tmp/smart-conlock-tuya-pycache python3 -m compileall -f custom_components/smart_conlock_tuya
git diff --check
```

No push should be made unless the user explicitly asks.

## Implemented So Far

Battery sensor:

- `sensor.<lock_name>_battery`
- Reads Tuya `battery_state`
- Raw states: `high`, `medium`, `low`, `poweroff`
- Attribute: `battery_percent_estimate`

`jtmspro` entities:

- `binary_sensor.<lock_name>_online`
- `binary_sensor.<lock_name>_call_active`
- Lock unlock is blocked unless the device is online and a recent request is active.

Request detection now uses a shared runtime/coordinator:

- Primary path: Tuya Device Status Notification MQTT messages.
- Fallback/debug path: Tuya report logs every 60 seconds.
- The 3-second entity-level REST polling path has been removed.

Report-log fallback endpoint:

- Endpoint: `GET /v2.1/cloud/thing/{device_id}/report-logs`
- Codes checked:
  - `doorbell`
  - `initiative_message`
  - `video_request_realtime`
  - `photo_again`
- Active window: 90 seconds

`initiative_message` is base64 decoded and treated as active when:

```json
{
  "cmd": "door_lock_video",
  "alarm": true
}
```

`video_request_realtime` is exposed as evidence only. Do not treat `AAABAQ==` and `AQABAQ==` as start/end until real-device behavior is confirmed.

`photo_again` is also diagnostic only.

## Latest Implementation Summary

Implemented on 2026-07-04:

- Added `custom_components/smart_conlock_tuya/runtime.py`.
- Added `DeviceStatusNotificationClient` using:
  - `POST /v1.0/iot-03/open-hub/access-config`
  - body fields: `uid`, `link_id`, `link_type: mqtt`, `topics: device`, `msg_encrypted_version: 2.0`
  - `paho-mqtt` for MQTT connection
  - AES-GCM decrypt logic matching Tuya OpenMQ custom mode.
- Added `OPEN_HUB_ACCESS_CONFIG_ENDPOINT` to `const.py`.
- Added `TuyaCloudApi.async_get_open_hub_access_config()`.
- Added `TuyaCloudApi.uid` property and fallback UID resolution from device details.
- Changed `manifest.json`:
  - `iot_class`: `cloud_push`
  - requirements: `paho-mqtt>=1.6.1`, `pycryptodome>=3.15.0`
- Restored the existing Call Active entity identity:
  - unique ID: `smart_conlock_tuya_{device_id}_call_active`
  - display name: `Call Active`
- Do not use `smart_conlock_tuya_{device_id}_video_call_request`; that caused Home Assistant to show the old Call Active entity as no longer provided.
- Runtime request events are filtered by configured `device_id`.
- `doorbell` and `initiative_message` only open the 90-second unlock window when the event has a real timestamp.
- Untimed/latest status values such as stale `doorbell: true` or `video_request_realtime: AQABAQ==` do not open the unlock window.
- `video_request_realtime` and `photo_again` are exposed as diagnostic attributes only.

Latest validation passed after this implementation:

```bash
env PYTHONPYCACHEPREFIX=/tmp/smart-conlock-tuya-pycache python3 -m compileall -f custom_components/smart_conlock_tuya
git diff --check
```

Also passed a local smoke test for:

- wrong `device_id` ignored
- untimed `doorbell` ignored
- `video_request_realtime` stored as diagnostic only
- timestamped valid `initiative_message` activates the request window
- nested `photo_again` payload parsing

## Important Correction From Prior Attempt

A prior implementation incorrectly tried to use Tuya SDK `TuyaOpenAPI.connect()` and `TuyaOpenMQ`.

Problem:

- SDK `connect()` is a username/password login flow, not the integration's existing `/v1.0/token?grant_type=1` Cloud project token flow.
- It can leave MQTT startup doing nothing or failing silently for this integration.

Corrected approach:

- Use the integration's existing signed Cloud API client.
- Request Open Hub MQTT access config directly with the existing token/signing flow.
- Use only the MQTT/decryption behavior from Tuya OpenMQ as a reference, not its login/bootstrap.

## Important Problem Found

Polling every 3 seconds was added briefly to make the UI react faster, but this is not a good production design.

Approximate API pressure if left as polling:

- Online sensor polls device details
- Video request sensor polls report logs
- Lock entity also polls online + report logs
- This can multiply API calls quickly and may hit Tuya Cloud quotas/rate limits.

The user correctly objected to this direction. Do not continue building around aggressive polling.

## Correct Direction

Use Tuya's **Device Status Notification** service.

The user showed that this service is already:

- Authorized project: `BJP HOME 2023`
- Expiration: Permanent
- Status: In service

This is the likely service needed for MQTT-style device status push messages. It is not SMS or Voice Message Service.

Target architecture:

1. Subscribe to Tuya Device Status Notification messages.
2. Listen for lock events:
   - `doorbell`
   - `initiative_message`
   - `video_request_realtime`
3. When a request event arrives, set `Call Active` to on immediately.
4. Open an unlock window for 90 seconds.
5. Make the lock entity available during that window if the device is online.
6. After the window expires, turn request state off and make unlock unavailable again.
7. Keep report logs only as fallback/debug, not the primary realtime path.

## Known Real Device Evidence

User provided Tuya event history:

```text
2026-07-03 21:57:01 Report Real-Time Video Call Request AQABAQ== device itself
2026-07-03 21:56:27 Report Active message push eyJ2IjoiNS4wIiwiY21kIjoiZG9vcl9sb2NrX3ZpZGVvIiwidHlwZSI6Im1lZGlhIiwid2l0aCI6InJlc291cmNlcyIsImFsYXJtIjp0cnVlLCJ0aW1lIjoxNzgzMDkwNTgzLCJmaWxlcyI6W1sidHktdXMtYml6bG9jayIsIi83MzIwOWItODI0NjA5NDAtOTQyN2QyODQzMmMzNTdlYS9jb21tb24vMTc4MzA5MDU4NjQzOF8xNzgzMDkwNTg0LmpwZyIsIjNmcXhrNWV3eTczZWN3eXMiLCIxNzg4Mjc0NTgzIl0sWyJ0eS11cy1iaXpsb2NrIiwiLzczMjA5Yi04MjQ2MDk0MC05NDI3ZDI4NDMyYzM1N2VhL2NvbW1vbi8xNzgzMDkwNTg2NDI5XzE3ODMwOTA1ODYubWpwZWciLCJoY21kbXk4eWR5ZDNuZ2R5IiwiMTc4MzM0OTc4MyJdXSwiZXh0Ijp7InJlY29yZCI6dHJ1ZSwiaWQiOjAsInR5cGUiOjIzLCJmaWxlSWQiOiIxNzYwODUzODM1In19 device itself
2026-07-03 21:56:25 Report Real-Time Video Call Request AAABAQ== device itself
2026-07-03 21:56:24 Report Real-Time Video Call Request AAABAQ== device itself
2026-07-03 21:56:23 Report Doorbell on device itself
2026-07-03 21:56:23 Report Doorbell on
```

The long base64 payload decodes to JSON with:

- `cmd`: `door_lock_video`
- `type`: `media`
- `alarm`: `true`
- media files including `.jpg` and `.mjpeg`

Device mapping from `Tuya/devices.json`:

- category: `jtmspro`
- `doorbell`: DP 19 Boolean
- `photo_again`: DP 47 Boolean
- `video_request_realtime`: DP 63 Raw
- `initiative_message`: DP 212 Raw

## Suggested Next Testing Plan

1. Restart Home Assistant and confirm the existing `Call Active` entity is provided again.
2. Check the `Call Active` attributes:
   - `diagnostic_status`
   - `last_error`
   - `report_log_error`
   - `doorbell`
   - `video_request_realtime`
   - `photo_again`
   - `initiative_message_decoded`
3. Confirm MQTT startup:
   - If `diagnostic_status` is `push_connected`, Device Status Notification is connected.
   - If `diagnostic_status` is `push_unavailable`, inspect `last_error`.
4. Press the lock doorbell and confirm:
   - `Call Active` turns on immediately or within the event path timing.
   - The lock entity becomes available only while the device is online and the 90-second request window is active.
   - `video_request_realtime` remains diagnostic only.
5. If MQTT does not connect, use the diagnostic attributes/logs to decide whether the access-config endpoint needs a different service permission, UID, region, or request body.

## Things To Avoid

- Do not push unless the user explicitly asks.
- Do not amend existing commits.
- Do not force push.
- Do not keep aggressive 3-second REST polling as production behavior.
- Do not assume Tuya video services are required for the doorbell/request event. The event path should come from Device Status Notification.

## 2026-07-04 Latest Runtime Findings

The `jtmspro` request-aware unlock flow now works through the report-log fallback.

Observed Home Assistant state after pressing the smart lock doorbell:

- `diagnostic_status`: `fallback_recent_request_detected`
- `report_log_error`: `null`
- `report_log_count`: `5`
- `source`: `initiative_message`
- `doorbell`: `"true"`
- `video_request_realtime`: `AAABAQ==`
- `initiative_message_decoded` contains:
  - `cmd`: `door_lock_video`
  - `type`: `media`
  - `alarm`: `true`
  - `files`: includes `.jpg` and `.mjpeg` resource paths
- `Call Active` turns on and the lock can be unlocked.

Important timing note:

- This is still slower than ideal because it is using the 60-second report-log fallback.
- It may arrive close to the end of the real device's active unlock window.
- The realtime push path is still not delivering messages to Home Assistant.

Push diagnostics showed:

- `push_connect_result`: `0`
- `push_topic_count`: `1`
- `push_subscribed_topic_count`: `1`
- `push_last_subscribe_status`: `ok`
- `push_message_count`: `0`

Interpretation:

- Device events reach Tuya Cloud.
- Home Assistant can get Open Hub MQTT config, connect, and subscribe.
- No MQTT messages are arriving on the subscribed topic, so the current problem is before decrypt/parse.
- Do not keep debugging decoded MQTT payload shape until `push_message_count` becomes nonzero.

## Changes Made In This Session

- Fixed Tuya report-log request signing/query handling so report logs can be read.
- Added push diagnostics for Open Hub MQTT connection, subscription, message, decode, and parser stages.
- Exposed push diagnostics as attributes on `binary_sensor.<lock_name>_call_active`.
- Made the MQTT event parser accept decoded payload roots that are lists.
- Made report-log fallback handle timestamp fields beyond only `event_time`.
- Made `initiative_message` fallback use decoded payload `time` when the report-log wrapper does not expose a usable timestamp.
- Confirmed report-log fallback can activate the 90-second unlock window from `initiative_message`.

Latest validation passed:

```bash
env PYTHONPYCACHEPREFIX=/tmp/smart-conlock-tuya-pycache python3 -m compileall -f custom_components/smart_conlock_tuya
git diff --check
```

## Lock State / History Work Continued

Implemented after reading these notes:

- Added `lock_motor_state` interpretation through `TuyaCloudApi.interpret_lock_motor_state()`.
- Added `TuyaCloudApi.async_get_lock_activity_state()`:
  - Reads current `/v1.0/iot-03/devices/{device_id}/status`.
  - Returns raw `lock_motor_state` and parsed Home Assistant `locked` state.
- Added runtime storage for:
  - `locked`
  - `lock_motor_state`
  - `lock_report_log_error`
  - `lock_report_log_count`
  - `last_lock_operation`
- Added push handling for `lock_motor_state` if Device Status Notification eventually starts delivering messages.
- Updated the lock entity so:
  - `jtmspro` lock state syncs from the shared runtime.
  - non-`jtmspro` lock entities can poll `lock_motor_state` through the existing status endpoint.
  - successful Home Assistant lock/unlock commands immediately update the visible state.
  - successful lock/unlock commands fire a Home Assistant event named `smart_conlock_tuya_lock_operation`.
- Added lock attributes for `jtmspro`:
  - `lock_motor_state`
  - `lock_report_log_error`
  - `lock_report_log_count`
  - `last_lock_operation`
- Important fix: do not request `lock_motor_state` through `/v2.1/cloud/thing/{device_id}/report-logs`; Tuya returns `param is illegal ,please check it`.
- Important correction: do not default to a 3-second auto-lock timer. The user's physical device can be configured to relock after a delay, but that setting does not appear to be exposed by Tuya's API for this product.
- Added integration option:
  - `device_relock_delay`
- Allowed values are `off`, `5`, `10`, and `15`, matching the physical lock's relock delay choices.
- Relock state is only scheduled when Tuya exposes `auto_lock_time` or the user configures `device_relock_delay` to match the physical lock.
- If no real locked/unlocked DP exists, the lock entity keeps the latest state the integration can actually know from successful Home Assistant commands or push events.

Latest validation passed:

```bash
env PYTHONPYCACHEPREFIX=/tmp/smart-conlock-tuya-pycache python3 -m compileall -f custom_components/smart_conlock_tuya
git diff --check
```

Important caveat:

- `/Users/bordin/Devs/Tuya/devices.json` did not include `lock_motor_state`, so real-device values still need to be confirmed in Home Assistant attributes.
- Current parser assumes Tuya boolean `true`/`1`/`open` means unlocked and `false`/`0`/`closed` means locked, matching the prior local helper comment. If the real device reports opposite values, invert this parser.
- The user's `jtmspro` mapping includes `unlock_fingerprint`, `unlock_password`, `unlock_temporary`, `unlock_card`, `unlock_face`, `unlock_app`, and `unlock_hand`, but those are unlock event/counter style datapoints, not proof that the device is currently locked.

## Latest Image Work Continued

User pointed out that the image evidence was arriving in `initiative_message_decoded.files`, but the integration was only exposing it as a raw attribute and not displaying it.

Implemented:

- Added `Platform.IMAGE` to the integration platforms.
- Added `custom_components/smart_conlock_tuya/image.py`.
- Added `image.<lock_name>_latest_image` for `jtmspro` devices.
- The image entity:
  - subscribes to the shared runtime;
  - extracts the still-image resource path from `initiative_message_decoded.files`;
  - calls `GET /v1.0/devices/{device_id}/door-lock/latest/media/url?file_type=1`;
  - recursively extracts a usable `http`/`https` URL from common Tuya response shapes;
  - proxies image bytes through Home Assistant with `async_image()`;
  - exposes diagnostics:
    - `latest_resource_path`
    - `latest_media_error`
    - `latest_media_result_keys`
    - `image_url_available`

Important caveat:

- The decoded `files` array contains Tuya resource paths, not necessarily browser-displayable URLs.
- The image entity can only display the still image if Tuya's latest-media endpoint returns a direct image URL.
- If `latest_media_error` is `latest_media_url_not_found` or `latest_media_unavailable`, inspect the Tuya API response/service permissions before adding more code.

## Next Feature Requests

The user wants to continue with these features next:

1. Add logs/history for lock and unlock operations.
   - Partially implemented via `last_lock_operation` for Home Assistant commands/push events and `smart_conlock_tuya_lock_operation` HA events.
   - Do not use report logs with `lock_motor_state`; Tuya rejects that code on the report-log endpoint.
   - Goal: show when the lock was locked/unlocked and, if possible, whether the operation came from Home Assistant, device/app, or Tuya report logs.
   - Likely sources to investigate: existing `lock.py` command result paths, Home Assistant logbook/event entities, and any Tuya report-log operation code that is explicitly accepted by the endpoint.

2. Show the current lock state: locked or unlocked.
   - Implemented conservatively using `lock_motor_state` only when present.
   - If `lock_motor_state` is absent, use latest known command/push state and only schedule relock when the user configures the delay that is set on the physical device.
   - Goal: expose a reliable Home Assistant lock state instead of only command capability.
   - Need to confirm whether `/v1.0/iot-03/devices/{device_id}/status` ever returns a real locked/unlocked DP for this product.

3. Show a still image from the smart lock camera.
   - Implemented as `image.<lock_name>_latest_image`, pending real-device validation.
   - Goal: image only, not video.
   - The decoded `initiative_message.files` includes `.jpg` resource paths.
   - Existing API helpers to investigate:
     - `GET /v1.0/devices/{device_id}/door-lock/latest/media/url?file_type=1`
     - `GET /v1.0/smart-lock/devices/{device_id}/albums-media`
   - Need to determine whether Tuya returns a directly usable signed image URL, encrypted media, or requires additional video/cloud-storage service permissions.

## 2026-07-04 Latest Correction: Unlock Control vs Physical Status

Important correction from the user:

- The `jtmspro` Tuya API can unlock the device, but it cannot lock the device.
- The device/product does not expose a reliable current physical locked/unlocked state in the known Standard Status Set.
- The existing Home Assistant lock toggle must not be treated as physical truth.

Implemented:

- The existing `lock.<device>` entity keeps the existing unique ID:
  - `smart_conlock_tuya_{device_id}`
- For `jtmspro`, this entity is now a **Remote Unlock Control**:
  - it exists to send the Tuya unlock command only;
  - it does not represent physical lock status;
  - it does not call the Tuya lock API from `async_lock()`;
  - `async_lock()` only resets the control state locally to ready/locked;
  - it stays internally `locked` so Home Assistant shows the next possible action as Unlock, even when disabled;
  - when `Call Active` is off or the device is offline, it is unavailable/disabled so users cannot press it randomly;
  - when the device is online and `Call Active` is on, it becomes available and `async_unlock()` can call the Tuya unlock API.
- The remote unlock control exposes diagnostic attributes:
  - `entity_role: remote_unlock_control`
  - `physical_status_entity`
  - `unlock_available`
  - `lock_available: false`
  - `tuya_lock_api_supported: false`
  - `command_state_source`
  - `call_active`

Added a separate manual physical status entity:

- Entity: `lock.<device>_physical_status`
- Unique ID:
  - `smart_conlock_tuya_{device_id}_physical_status`
- Purpose:
  - user-maintained physical locked/unlocked status;
  - can be changed manually in Home Assistant;
  - can be changed by automations from other sensors;
  - restores state after Home Assistant restart;
  - changes to `unlocked` after a successful remote unlock command.
- If Tuya ever exposes a real physical state DP such as `lock_motor_state`, runtime can update this physical status entity only when `state_confidence` is `physical_dp`.
- Manual changes are preserved over older remote unlock operations; a later successful remote unlock can update Physical Status again.

Important behavior to preserve:

- Do not make the existing `jtmspro` lock entity available when there is no active call.
- Do not call `TuyaCloudApi.async_lock()` for `jtmspro`.
- Do not use `unlock_fingerprint`, `unlock_password`, `unlock_card`, `unlock_face`, `unlock_app`, or `unlock_hand` as proof of current physical lock status. They are event/counter style datapoints.

## 2026-07-04 Latest Correction: Request-Only Fast Fallback

Problem:

- Tuya Device Status Notification can connect and subscribe, but real device events still have `push_message_count: 0`.
- The image/media evidence can arrive in report logs before `Call Active` updates, so waiting for the 60-second full fallback is too slow.

Implemented:

- Full fallback remains every 60 seconds for broader device/online/status diagnostics.
- Added request-only fast fallback:
  - interval: 5 seconds;
  - refreshes only the request/call report-log state;
  - does not fetch full device status each time;
  - runs when push is unavailable, not ready, or connected/subscribed but silent;
  - stops automatically once push messages start arriving.
- `binary_sensor.<lock_name>_call_active` now exposes:
  - `request_fast_fallback_active`
  - `request_fast_fallback_reason`
  - `request_fast_fallback_interval`
  - `request_last_refresh_time`

Latest validation passed:

```bash
env PYTHONPYCACHEPREFIX=/tmp/smart-conlock-tuya-pycache python3 -m compileall -f custom_components/smart_conlock_tuya
git diff --check
```
