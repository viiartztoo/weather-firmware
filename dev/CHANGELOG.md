# Outdoor Sensor Firmware — Changelog

History lives here so the source files can stay comment-free. Per-file versions
are kept in code as `__version__` / `VERSION` and listed in `MANIFEST.txt`
(run `python dev/gen_manifest.py`). Strip comments with `python dev/strip_comments.py`.

## Bundle history

### 1.5.1 — 2026-07-19
- Dashboard: the raw MQTT payload is now pretty-printed (client-side JS), and the
  "Check for update" button is centered.
- OTA UX: the /ota page now does check -> show result -> apply. Clicking "Check
  for update" reports current vs available version; if newer, an "Update now"
  button performs the download/reboot. No more ambiguous "will reboot if..."
  message. ota.py gains a lightweight check() (manifest only, no download).

### 1.5.0 — 2026-07-19
- OTA updates (ota.py 2.0.0): pulls from a **private** GitHub repo via the REST
  API with a token in the Authorization header. `gc.collect()` + one-file-at-a-
  time downloads + web-server pause during the TLS transfer (same lesson as the
  Firebase offload). Triggers: manual "Check for update" button on the device
  page (`/ota`), and optional auto check (`ota.auto` + `check_interval`).
- Dashboard is now a real file (`dashboard.html`), loaded once at boot and
  filled with `{{TOKENS}}` per request — easy to edit and OTA-updatable.
- config `ota` block reworked: `repo`, `branch`, `token`, `auto`. version.json
  now lists main.py, my_hw.py, ota.py, tools.py, dashboard.html.

### 1.4.0 — 2026-07-19
- New onboard web dashboard: styled cards (temp/humidity/pressure), a metadata
  table (updated/uptime/IP/host), and the **exact raw JSON last published to
  MQTT** shown in a code block. `/mqtt_data` now returns that verbatim payload
  (tracked in `last_mqtt_payload`), `/data` still serves the compact JSON.

### 1.3.9 — 2026-07-19
- Removed the on-device Firebase code entirely (`firebase_uploader.py` deleted,
  import/calls and the `firebase` config block gone) now that Node-RED handles
  the upload. The ESP32 publishes only MQTT. OTA file list updated.

### 1.3.8 — 2026-07-19
- Firebase upload offloaded to Node-RED. On-device TLS was unreliable on the
  ESP32 (heap fragmentation + web-server GIL contention during the handshake),
  so `firebase.enabled` is now false: the ESP32 only publishes MQTT, and a
  Node-RED flow (see `Node-RED Firebase/`) does the Firebase POST.

### 1.3.7 — 2026-07-19
- Startup self-test: after "Ready", the device does one full read and publishes
  to both MQTT and Firebase before entering the loop, so the whole chain is
  exercised immediately. The forced Firebase post also seeds the upload
  interval so the loop doesn't double-post.

### 1.3.6 — 2026-07-19
- Initial sensor read at startup: values are shown in the boot block (`Read :`)
  and `latest_sensor_data` is populated immediately, so the web status page has
  real data at once instead of zeros until the first loop.

### 1.3.5 — 2026-07-19
- Cleaner boot output: quiet-by-default logging with a `logging.verbose` config
  toggle. Repeated config-load lines, per-manager init/version tags, and
  duplicate web/time lines are now debug-only. Startup prints a tidy aligned
  block (`WiFi / Time / MQTT / Cloud / Sensor / Web / WDT / OTA / Ready`).

### 1.3.4 — 2026-07-19
- Onboard web server slimmed down: handles requests inline (no per-request
  threads) and serves a tiny in-memory status page instead of reading the
  10 KB `dashboard.html` (now removed). Keeps an on-device view while leaving
  RAM for the Firebase TLS handshake. Toggle via `config.json` `webserver.enabled`.

### 1.3.3 — 2026-07-19
- Memory hardening: `gc.collect()` before each Firebase TLS attempt and once per
  loop, fixing `ENOMEM` / `MBEDTLS_ERR_X509_ALLOC_FAILED` and the flaky server.
- Failure logs now print free heap.

### 1.3.2 — 2026-07-19
- Watchdog is config-driven (`config.json` `watchdog.enabled` / `timeout_ms`)
  so it can be disabled for dev/debugging without editing code.

### 1.3.1 — 2026-07-19
- NTP time sync at startup (`TimeSync`) so readings carry real local timestamps
  instead of the year-2000 boot clock. Tries an AU server first, then a global
  fallback (`time.ntp_hosts`), with a configurable `utc_offset_hours`.

### 1.3.0 — 2026-07-19
- Direct upload to Firebase Realtime Database, bypassing Node-RED for the cloud
  path (local MQTT publishing unchanged).
- GitHub-based OTA updates via `ota.py` (opt-in).
- Based on Current Build (1.2.0).

## Earlier snapshots (pre-Firebase, from the version folders)

- **1.2.0** (Current Build, 2026-01-06) — MQTT publishing + onboard web server baseline.
- **1.0.3** (2026-01-07) — fixed uptime.
- **1.0.2** (2026-01-06).
- **1.0.0** (2025-04-21) — initial release.

## Current per-module versions

| File | Version | Notes |
|------|---------|-------|
| main.py | 1.3.5 | app entry, loop, lightweight web server, clean logging |
| my_hw.py | 1.1.0 | hardware/WiFi/MQTT + TimeSync |
| firebase_uploader.py | 1.1.0 | RTDB upload, gc before TLS |
| ota.py | 1.0.0 | GitHub OTA |
| tools.py | 1.1.0 | local file-change reboot |
| boot.py | 1.0.0 | boot + WebREPL |
| webrepl_cfg.py | 1.0.0 | WebREPL password |
| PiicoDev_BME280.py | 1.0.0 | vendored (Core Electronics) |
| PiicoDev_Unified.py | 1.1.0 | vendored (Core Electronics) |
