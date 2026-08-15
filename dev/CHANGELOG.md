# Outdoor Sensor Firmware — Changelog

History lives here so the source files can stay comment-free. Per-file versions
are kept in code as `__version__` / `VERSION` and listed in `MANIFEST.txt`
(run `python dev/gen_manifest.py`). Strip comments with `python dev/strip_comments.py`.

## Bundle history

### 2.0.2 — 2026-08-15
Two bugs found by running the bytecode build on real hardware.

- **Leaked listening socket.** If anything failed after `bind()`, `start()`
  closed `self.socket` - still `None` at that point - so the bound socket
  leaked and held port 80. Every retry then failed `EADDRINUSE` forever, which
  read as "the web server is broken". It now closes the local socket, and uses
  `setblocking(False)` rather than `settimeout(0)`.
- **`_versions()` restarted the application.** It did `import main` to read the
  stub's version, but `main.py` calls `app.run()` - so importing it ran the
  whole app a second time, and the second instance could not bind port 80. The
  stub version is now read from the file as text.

### 2.0.0 — 2026-08-15
The firmware now ships as **precompiled bytecode**, and that is what finally
fixed a day of failures blamed on two different boards.

**Root cause.** Source is parsed into RAM at import. The application had grown
to ~91 KB of source across six modules, and that footprint starved the pools
FreeRTOS and lwIP allocate from — so `_thread.start_new_thread()` failed with
"can't create thread" at *any* stack size down to 3 KB, and every outbound TCP
connect returned `-203` while UDP and DNS kept working. `gc.mem_free()` still
reported ~72 KB free, which is why it read as a hardware fault for hours.

It was measured conclusively by testing the same board bare: threads and TCP
worked perfectly with nothing loaded, and failed the moment the firmware was
imported. Both boards were healthy the whole time.

- **Bytecode bundle**: 91,460 bytes of source → 37,996 bytes of `.mpy`, 58%
  smaller. Compile with `mpy-cross==1.28.0.post2` (must match MicroPython
  1.28.0's bytecode version).
- **`main.py` is now a 138-byte stub** that imports `app`. MicroPython must run
  a real `.py` at boot, so the application moved to `app.py` → `app.mpy`.
- **No threads.** The web server's accept loop moved into the main loop as
  `poll()`, called ~10x/second between readings. Threads were the single
  largest allocation and the first thing to fail.
- **Per-file versions reported at runtime**: a `Modules:` line at boot listing
  app, my_hw, ota, tools, dashboard and the stub, and the same set served under
  `versions` on `/health`. `tools.py` gained the `__version__` it was missing.
- `version.json` corrected to match `main.py` — `gen_manifest.py` caught that
  they had drifted (1.7.0 vs 2.0.0).

**Known gap:** OTA of `.mpy` files is untested. The Node-RED proxy fetches from
the GitHub contents API and binary payloads may need base64 handling. Verify a
downloaded file byte-for-byte before trusting OTA again. The manifest is pinned
at 2.0.0 — the same as the device — so nothing is offered until bumped.

### 1.7.0 — 2026-08-15
Fixes a 20-day silent outage: the device stayed up, read the sensor, served its
web page and looked healthy while publishing into a dead socket. Nothing on the
device or the cloud dashboard indicated a problem.

- **MQTTManager rewritten (2.0.0).** Three faults made the old failure permanent:
  - `publish()` errors were only printed. It now returns a bool, marks the link
    dead on failure, and logs an event.
  - `_handle_mqtt_error()` called `disconnect()`, which ran `wlan.active(False)`
    — MQTT recovery switched off the WiFi radio. `_close()` now drops only the
    MQTT socket and never touches the network interface.
  - `connect_mqtt()` tested `client.is_connected()`, which `umqtt.simple` does
    not implement, so recovery raised `AttributeError`. Connection state is now
    tracked in `self.connected`.
- **Non-blocking reconnect** via `ensure_connected()`, safe to call every loop,
  with 5 s → 5 min exponential backoff. A broker that is down no longer blocks
  the main loop or stops the device booting.
- **Last will and testament**: retained `online` / `offline` on
  `outdoor_sensor/BME280/status`, so the broker announces the device dropping
  off. This is what makes external alerting possible.
- **Periodic ping** every 10th cycle (~5 min) to catch a half-open socket that
  still accepts writes.
- **WiFi supervision** in the main loop, with reconnect and event logging. There
  was previously none at all after boot.
- **EventLog (my_hw 1.2.0)**: a 40-entry ring buffer of state changes only —
  boot (with reset cause), WiFi up/down, MQTT up/down, publish resumed. Mirrored
  to `events.json`, so it survives a reboot and can explain an unexplained one.
- **Dashboard 1.3.0**: a status banner that goes amber/red when publishing
  stalls, a Connection panel (WiFi + RSSI, MQTT state, last publish age,
  ok/failed/reconnect counts), and a Recent events table.
- **New endpoints** `/health` (JSON, with a single `healthy` boolean) and
  `/events`, for Node-RED or any external watcher to poll.

Note: alerting still needs something off-device. A device that is unpowered or
wedged cannot report its own failure — use the last-will topic or poll `/health`.

### 1.6.0 — 2026-07-19
- Device settings page (`/settings`): edit the watchdog (enable + timeout) and
  verbose logging from the browser. Saving writes config.json atomically and
  reboots to apply. Added a plain "Reboot now" button (`/reboot`) and a Settings
  link on the dashboard.

### 1.5.3 — 2026-07-19
- Version bump to exercise the HTTP OTA path end-to-end (download + reboot).

### 1.5.2 — 2026-07-19
- OTA reworked to pull over plain HTTP from a LAN proxy (ota.py 3.0.0). On-device
  TLS to GitHub failed with heap fragmentation (`MBEDTLS_ERR_X509_ALLOC_FAILED`
  even at 90 KB free), so a Node-RED flow now proxies the private repo: ESP32 →
  HTTP → Node-RED → HTTPS → GitHub. No TLS/token on the device. Config `ota` uses
  `base_url` instead of `repo`/`token`. See `Node-RED Firebase/ota-proxy-flow.json`.

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
