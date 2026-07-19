# Firmware v1.3.0 — Firebase + OTA

Based on **Current Build (v1.2.0)**. Local MQTT publishing and the onboard web
server are unchanged. Two additions:

## 1. Direct Firebase upload (`firebase_uploader.py`)
The ESP32 POSTs each reading straight to Firebase Realtime Database, in parallel
with MQTT (Node-RED is not needed for the cloud path). Runs on its own interval
(default 5 min), independent of the 30 s MQTT loop. Data lands under
`readings/<pushId>` and is read by the Firebase-hosted dashboard.

Includes a retry loop that **feeds the 40 s watchdog between attempts**, so a
transient WiFi/DNS hiccup can't reboot the device mid-upload.

Configure in `config.json`:
```json
"firebase": {
    "enabled": true,
    "url": "https://myiot-f46a0-default-rtdb.firebaseio.com",
    "path": "readings",
    "auth_token": "",
    "interval": 300, "retries": 3, "timeout": 15, "retry_wait": 5
}
```
Set `auth_token` to your Firebase database secret once you lock down write rules
(see the dashboard README). Leave `""` only while rules allow open writes.

## 2. GitHub OTA updates (`ota.py`)
Checks `version.json` in your GitHub repo on an interval (default daily). If the
remote `version` is newer than the firmware's `__version__`, it downloads the
listed files to `*.new`, swaps them in only after **all** succeed, then reboots.
Complements the existing `file_change_check()` (which reboots on local edits).

Configure in `config.json` (disabled by default):
```json
"ota": {
    "enabled": false,
    "base_url": "https://raw.githubusercontent.com/viiartztoo/weather-firmware/main/",
    "manifest": "version.json",
    "check_interval": 86400
}
```

To publish an update: bump `__version__` in `main.py`, update `version.json`
(`version` + `files`), push to GitHub. Devices pick it up on their next check.

## Flash order
Copy all files to the ESP32. New files: `firebase_uploader.py`, `ota.py`,
`version.json`. Edit `config.json` with your real Firebase `url`/`auth_token`
before enabling.

## ⚠️ Security note
`config.json` holds WiFi/MQTT passwords and the Firebase token in plaintext.
Keep the `weather-firmware` repo **private** (it's what OTA pulls from), or split
secrets into a git-ignored `secrets.json`.
