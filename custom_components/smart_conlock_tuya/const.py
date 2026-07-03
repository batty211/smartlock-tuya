"""Constants for Smart (Con)lock tuya."""

DOMAIN = "smart_conlock_tuya"

CONF_ACCESS_ID = "access_id"
CONF_ACCESS_SECRET = "access_secret"
CONF_DEVICE_ID = "device_id"
CONF_DEVICE_NAME = "device_name"
CONF_DEVICE_CATEGORY = "device_category"
CONF_API_REGION = "api_region"

API_REGIONS = {
    "eu": "openapi.tuyaeu.com",
    "us": "openapi.tuyaus.com",
    "cn": "openapi.tuyacn.com",
    "in": "openapi.tuyain.com",
}

# Tuya device categories that are locks / access control
LOCK_CATEGORIES = {
    "mk",        # Access control
    "ms",        # Smart lock
    "jtmsbh",    # Smart lock (legacy)
    "jtmspro",   # Smart lock pro
    "gyms",      # Gym locker
    "hotelms",   # Hotel lock
    "videolock", # Video lock
    "photolock", # Photo lock
}

TICKET_ENDPOINT = "/v1.0/devices/{device_id}/door-lock/password-ticket"
DOOR_OPERATE_ENDPOINT = "/v1.0/smart-lock/devices/{device_id}/password-free/door-operate"
STATUS_ENDPOINT = "/v1.0/iot-03/devices/{device_id}/status"
DEVICE_DETAILS_ENDPOINT = "/v1.0/devices/{device_id}"
SPECIFICATIONS_ENDPOINT = "/v1.0/devices/{device_id}/specifications"
DEVICES_ENDPOINT = "/v1.0/users/{uid}/devices"
REMOTE_UNLOCKS_ENDPOINT = "/v1.0/devices/{device_id}/door-lock/remote-unlocks"
STREAM_ALLOCATE_ENDPOINT = "/v1.0/devices/{device_id}/stream/actions/allocate"
WEBRTC_CONFIG_ENDPOINT = "/v1.0/devices/{device_id}/webrtc-configs"
LATEST_MEDIA_ENDPOINT = "/v1.0/devices/{device_id}/door-lock/latest/media/url"
ALBUMS_MEDIA_ENDPOINT = "/v1.0/smart-lock/devices/{device_id}/albums-media"
REPORT_LOGS_ENDPOINT = "/v2.1/cloud/thing/{device_id}/report-logs"
