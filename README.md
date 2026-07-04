# Smart (Con)lock tuya

[![HACS](https://img.shields.io/badge/HACS-Custom-orange?style=flat-square)](https://hacs.xyz/)
[![GitHub Release](https://img.shields.io/github/v/release/batty211/smartlock-tuya?style=flat-square)](https://github.com/batty211/smartlock-tuya/releases)
[![License](https://img.shields.io/github/license/batty211/smartlock-tuya?style=flat-square)](LICENSE)

**Control your Tuya smart lock directly from Home Assistant.**

The official Tuya integration doesn't support lock/unlock — it only exposes a binary sensor (open/closed state). This integration fills the gap by using the Tuya Smart Lock Cloud API to add a proper `lock` entity with lock/unlock control, battery status, and extra support for `jtmspro` video smart locks.

## Why this integration?

The official Home Assistant Tuya integration uses the `tuya-device-sharing-sdk` which does not implement the Smart Lock API. Lock devices (categories `mk`, `ms`, `jtmsbh`, etc.) only get a `binary_sensor` with no control capability.

Smart (Con)lock tuya uses the Cloud API ticket-based flow to send lock/unlock commands — the same mechanism the Tuya and Smart Life mobile apps use.

## What you get

| Entity | Type | What it does |
|--------|------|--------------|
| Lock | `lock` | Lock and unlock your door via Tuya Cloud API |
| Battery | `sensor` | Shows the raw Tuya battery enum (`high`, `medium`, `low`, `poweroff`) and an estimated percentage attribute |
| Online | `binary_sensor` | Shows whether a `jtmspro` device is online |
| Call Active | `binary_sensor` | Shows whether a recent `jtmspro` doorbell/video request opened the unlock window |

The lock entity is linked to your existing Tuya device in Home Assistant. It appears alongside the `binary_sensor` from the official Tuya integration, all grouped under the same device.

For `jtmspro` devices, unlock is blocked unless the device is online and an active video call/session is detected. This is intentionally conservative for video locks.

## Prerequisites

Before installing, you need to set up a few things on the Tuya IoT Platform. This takes about 10 minutes.

### 1. Create a Tuya IoT project

1. Go to [iot.tuya.com](https://iot.tuya.com) and create an account (or log in)
2. Go to **Cloud** > **Development** > **Create Cloud Project**
3. Give it a name (e.g. "Home Assistant")
4. Select the **Data Center** that matches your region (Western Europe, US East, etc.)
5. For **Development Method**, select **Smart Home**
6. Click **Create**

### 2. Link your Tuya / Smart Life app account

1. In your project, go to **Devices** > **Link Tuya App Account**
2. Click **Add App Account**
3. Open the Tuya or Smart Life app on your phone
4. Go to **Me** > tap the scan icon in the top right
5. Scan the QR code displayed on iot.tuya.com
6. Confirm the linking in the app
7. Your devices should now appear in the **All Devices** tab

### 3. Subscribe to required API services

1. In your project, go to **Service API**
2. Click **Go to Authorize** (or find the service list)
3. Search for and subscribe to **IoT Core** (Free Trial)
4. Search for and subscribe to **Smart Lock Open Service** (Free Trial)
5. For `jtmspro` realtime doorbell/request detection, subscribe to **Device Status Notification**.
6. For video lock investigation features, you may also need **IoT Video Live Stream** and **Video Cloud Storage** depending on your device and Tuya account.

Both services are free for personal use. They may require periodic renewal (every ~6 months) — you'll get an email when it's time.

### 4. Enable remote unlock on your device

1. Open the **Tuya** or **Smart Life** app on your phone
2. Go to your lock device
3. Open the device **Settings**
4. Enable **Remote Unlock** (sometimes called "Remote Unlock Without Password")

### 5. Note your credentials

1. Go back to [iot.tuya.com](https://iot.tuya.com) > **Cloud** > your project > **Overview**
2. Copy your **Access ID** and **Access Secret** — you'll need them during setup

## Installation

### HACS (recommended)

1. Open HACS in Home Assistant
2. Click the 3 dots menu > **Custom repositories**
3. Add `batty211/smartlock-tuya` as **Integration**
4. Search for and install **Smart (Con)lock tuya**
5. Restart Home Assistant
6. Go to **Settings** > **Integrations** > **Add Integration** > search for **Smart (Con)lock tuya**

### Manual

1. Copy the `custom_components/smart_conlock_tuya` folder to your Home Assistant `custom_components/` directory
2. Restart Home Assistant
3. Go to **Settings** > **Integrations** > **Add Integration** > search for **Smart (Con)lock tuya**

## Configuration

The integration uses a UI-based config flow — no YAML needed.

### Step 1: Enter your Tuya credentials

- **Access ID**: from your Tuya IoT project overview
- **Access Secret**: from your Tuya IoT project overview
- **API Region**: select the region matching your Tuya data center

### Step 2: Select your device

The integration will automatically discover lock devices linked to your Tuya account. Select the device you want to control.

If you have multiple locks, add the integration once per device.

## Battery sensor

The integration exposes `sensor.<lock_name>_battery` using the Tuya `battery_state` datapoint.

The sensor state is the raw Tuya enum:

- `high`
- `medium`
- `low`
- `poweroff`

It also exposes `battery_percent_estimate`:

| Tuya state | Estimate |
|------------|----------|
| `high` | 75 |
| `medium` | 50 |
| `low` | 20 |
| `poweroff` | 0 |

## jtmspro video smart locks

For category `jtmspro`, the integration adds:

- `binary_sensor.<lock_name>_online`
- `binary_sensor.<lock_name>_call_active`

The online sensor uses Tuya Device Status Notification when available, with slow REST refresh from `GET /v1.0/devices/{device_id}` as fallback.

The Call Active sensor is event-driven. It listens for Tuya Device Status Notification messages for `doorbell` and `initiative_message`, and opens a 90-second unlock window when a valid request arrives. `video_request_realtime` and `photo_again` are exposed as debugging evidence only until real-device start/end behavior is confirmed.

Recent Tuya report logs are kept as a slow 60-second fallback/debug path. If push or report-log fallback cannot be read, the Call Active sensor exposes `diagnostic_status`, `last_error`, and `report_log_error` attributes.

Unlock protection for `jtmspro`:

- If the device is not online, unlock is refused.
- If no recent doorbell/video request is detected, unlock is refused and the lock entity becomes unavailable so the Home Assistant unlock button cannot be pressed.
- Other lock categories keep the original unlock behavior.

### Video and media investigation

The API client includes helper methods for Tuya video/media endpoints, but this integration does not expose a Home Assistant camera entity yet.

Relevant Tuya APIs:

- `POST /v1.0/devices/{device_id}/stream/actions/allocate`
- `GET /v1.0/devices/{device_id}/webrtc-configs`
- `GET /v1.0/devices/{device_id}/door-lock/latest/media/url?file_type=1`
- `GET /v1.0/smart-lock/devices/{device_id}/albums-media`

Tuya media may be encrypted and may require additional Tuya API service subscriptions. These video services are for stream/media access and are not required for doorbell request event detection when Device Status Notification is enabled.

## Supported devices

**Tested:**
- Tuya Access Control (category `mk`, model WIFI_A)

**Should work with any Tuya lock/access control device that supports the Smart Lock Cloud API, including categories:**
- `mk` — Access control
- `ms` — Smart lock
- `jtmsbh` — Smart lock (legacy)
- `jtmspro` — Smart lock pro
- `gyms` — Gym locker
- `hotelms` — Hotel lock
- `videolock` — Video lock
- `photolock` — Photo lock

If your lock device uses the Tuya ticket-based unlock flow, it should work. If it doesn't, please [open an issue](https://github.com/batty211/smartlock-tuya/issues) with your device model and category.

## Limitations

- **Cloud-only**: Tuya locks do not support local control. Commands go through the Tuya Cloud API. If your internet is down, you can still use the physical keypad/badge/fingerprint on the device itself.
- **API trial renewal**: IoT Core and Smart Lock Open Service are free but require renewal approximately every 6 months on iot.tuya.com.
- **Push-based request detection**: `jtmspro` request state uses Tuya Device Status Notification. Report logs are only a slow fallback/debug path.
- **Video call detection**: `jtmspro` call detection is based on Tuya datapoints, not a dedicated doorbell session API.
- **Camera entity not included**: Stream, WebRTC, latest media, and album APIs are available as investigation helpers only.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `uri path invalid` on lock/unlock | Your IoT Core or Smart Lock Open Service subscription has expired. Renew it on [iot.tuya.com](https://iot.tuya.com). |
| `permission deny` | Your Smart Life / Tuya app account is not linked to the IoT project. See Prerequisites step 2. |
| No devices found during setup | Make sure your app account is linked and your device is a supported lock category. |
| Unlock command succeeds but door doesn't open | Enable Remote Unlock in the Tuya / Smart Life app settings for your device. |
| `jtmspro` unlock is refused | Make sure the lock is online and that a recent doorbell/video request is active. Check the Call Active sensor attributes for `diagnostic_status`, `report_log_error`, and raw datapoint values. |
| No video stream or media URL | Confirm your Tuya project has the required video/media API services enabled and test the endpoint in Tuya API Explorer. |
| `invalid_auth` during setup | Double-check your Access ID and Access Secret. Make sure you're using the credentials from the correct project. |

## Credits

Forked from [`nicolasglg/tuya-smart-lock`](https://github.com/nicolasglg/tuya-smart-lock).

## License

This fork keeps the original upstream MIT License and copyright notice.
See [LICENSE](LICENSE) for details.
