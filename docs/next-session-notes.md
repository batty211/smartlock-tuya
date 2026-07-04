# Next Session Notes

Date: 2026-07-03

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
- `binary_sensor.<lock_name>_video_call_request`
- Lock unlock is blocked unless the device is online and a recent request is active.

Request detection currently uses Tuya report logs:

- Endpoint: `GET /v2.1/cloud/thing/{device_id}/report-logs`
- Codes checked:
  - `doorbell`
  - `initiative_message`
  - `video_request_realtime`
- Active window: 90 seconds

`initiative_message` is base64 decoded and treated as active when:

```json
{
  "cmd": "door_lock_video",
  "alarm": true
}
```

`video_request_realtime` is exposed as evidence only. Do not treat `AAABAQ==` and `AQABAQ==` as start/end until real-device behavior is confirmed.

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
3. When a request event arrives, set `Video Call Request` to on immediately.
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

## Suggested Next Implementation Plan

1. Revert or replace 3-second polling before release.
2. Add a shared runtime state object/coordinator for this integration entry:
   - online state
   - request active state
   - request expiration timestamp
   - last event payload
   - diagnostic status
3. Add Tuya Device Status Notification MQTT client support.
4. On message:
   - filter by `device_id`
   - parse DP code/value
   - detect request event
   - update coordinator state
   - call `async_write_ha_state()` on affected entities
5. Add a fallback polling mode only if MQTT is unavailable:
   - online: slow interval such as 60 seconds
   - report logs: either manual/debug or slow fallback, not 3 seconds
6. Update README:
   - Device Status Notification is required for realtime request detection
   - report logs are fallback/debug
   - video services are only needed for stream/media, not doorbell event detection

## Things To Avoid

- Do not push unless the user explicitly asks.
- Do not amend existing commits.
- Do not force push.
- Do not keep aggressive 3-second REST polling as production behavior.
- Do not assume Tuya video services are required for the doorbell/request event. The event path should come from Device Status Notification.

