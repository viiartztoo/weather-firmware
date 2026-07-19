# OTA setup (private GitHub repo)

The device pulls updated files from a **private** GitHub repo using the REST API
with a personal-access token. It supports a manual "Check for update" button on
the device page and an optional automatic check.

## How it works

1. Device reads `version.json` from the repo (`GET /repos/OWNER/REPO/contents/version.json`).
2. If the repo `version` is newer than the running `main.py` `__version__`, it
   downloads each file in `version.json`'s `files` list to `*.new`.
3. Only after **all** download OK does it swap them in and reboot.
4. During the download the onboard web server is paused (frees RAM/CPU for TLS).

## One-time setup

### 1. Create a fine-grained personal access token
- GitHub ▸ **Settings ▸ Developer settings ▸ Personal access tokens ▸
  Fine-grained tokens ▸ Generate new token**.
- **Repository access**: Only select repositories → `weather-firmware`.
- **Permissions**: Repository permissions ▸ **Contents: Read-only**.
- Generate and copy the token (starts with `github_pat_...`).

> Read-only + single-repo keeps the blast radius tiny if the token leaks.

### 2. Put the token in the device config
In `config.json` ▸ `ota`:
```json
"ota": {
    "enabled": true,
    "auto": false,
    "repo": "viiartztoo/weather-firmware",
    "branch": "main",
    "token": "github_pat_XXXXXXXX",
    "manifest": "version.json",
    "check_interval": 86400,
    "timeout": 20
}
```
- `enabled`: master switch.
- `auto`: `false` = manual only (button); `true` = also self-check every
  `check_interval` seconds.

### 3. Push the current firmware to the repo
The repo must contain the files listed in `version.json` at its root. From the
firmware folder:
```bash
git add -A
git commit -m "Firmware 1.5.0"
git push
```
Confirm on GitHub that `version.json`, `main.py`, `my_hw.py`, `ota.py`,
`tools.py`, `dashboard.html` are at the repo root.

> `config.json` is intentionally NOT in `version.json`'s file list, so OTA never
> overwrites the device's own settings/secrets.

## Publishing an update

1. Edit code, bump the file's `@v` tag and `main.py` `__version__`.
2. Bump `version` in `version.json` (and its `files` list if you added/renamed).
3. `git commit` + `git push`.
4. On the device: open the page and click **Check for update** (or wait for the
   auto check). It downloads, swaps, and reboots into the new version.

## Testing checklist

- With `enabled:true`, click **Check for update** while the running version is
  older than the repo. Serial should show:
  `[OTA] update available: X -> Y`, `[OTA] downloading main.py ...`,
  `[OTA] updated to Y - rebooting`.
- If already current: `[OTA] up to date (local Y, remote Y)`.
- Auth failure (bad/expired token or wrong repo): `[OTA] manifest HTTP 404`
  (private repos return 404, not 401, when unauthorized) — recheck the token
  and `repo`.

## Notes / gotchas

- OTA does a TLS download on the ESP32 — heavy. Keep the web server pause in
  place; don't hammer the device page during an update.
- A bad `main.py` (syntax error) would boot-loop. Test changes on a spare board
  or keep a known-good copy handy. (A future enhancement could add a rollback.)
- GitHub private-repo raw access via `raw.githubusercontent.com` needs temporary
  tokens; this uses the API contents endpoint instead, which works with a PAT.
