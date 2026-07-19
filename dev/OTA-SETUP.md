# OTA setup (via Node-RED proxy)

The ESP32 can't do a reliable TLS handshake to GitHub (heap fragmentation -
`MBEDTLS_ERR_X509_ALLOC_FAILED` even with 90 KB free). So OTA pulls firmware over
plain **HTTP from the LAN**, and Node-RED does the GitHub HTTPS.

```
ESP32 --HTTP--> Node-RED /firmware/<file> --HTTPS--> GitHub (private repo)
```

- No TLS on the device, no token on the device.
- GitHub stays the source of truth; the token lives only in Node-RED.

## One-time setup

### 1. Node-RED: import the OTA proxy flow
- Import `Node-RED Firebase/ota-proxy-flow.json` (☰ ▸ Import).
- It adds an `OTA Proxy` tab: **http in `/firmware/:file` → function → http request → http response**.

### 2. Give Node-RED the GitHub token
The function node needs a fine-grained PAT (single repo `weather-firmware`,
**Contents: Read-only**). Either:
- set a Node-RED environment variable `GITHUB_TOKEN`, or
- open the **Build GitHub URL** function node and replace `REPLACE_WITH_GITHUB_TOKEN`.

> If your old token was pasted in chat, revoke it on GitHub and make a new one.

Then **Deploy**.

### 3. Test the proxy from your PC
Open in a browser (or curl):
```
http://192.168.1.40:1880/endpoint/firmware/version.json
```
You should get the raw `version.json` from the repo. If you get 404/401, the
token or repo path in the function node is wrong.

> **Home Assistant add-on:** the HA Node-RED add-on serves all http-in nodes
> under an `/endpoint/` prefix (httpNodeRoot). So the node URL `/firmware/:file`
> is reached at `.../endpoint/firmware/<file>`. Standalone Node-RED has no such
> prefix (`.../firmware/<file>`). Set `base_url` to match your setup.

### 4. Point the device at the proxy
`config.json` ▸ `ota` (already set):
```json
"ota": {
    "enabled": true,
    "auto": false,
    "base_url": "http://192.168.1.40:1880/firmware/",
    "manifest": "version.json",
    "check_interval": 86400,
    "timeout": 20
}
```
Adjust the IP/port if your Node-RED isn't at `192.168.1.40:1880`.

### 5. Push firmware to the repo
The repo must contain the files listed in `version.json` at its root:
```bash
git add -A && git commit -m "Firmware 1.5.2" && git push
```

## Using it

On the device page click **Check for update**:
- **Up to date · vX** - nothing to do.
- **Update available · vX -> vY** - click **Update now**; it downloads over HTTP
  and reboots.

Serial shows: `[OTA] update available: X -> Y`, `[OTA] downloading main.py`, ...,
`[OTA] updated to Y - rebooting`. (It may reboot twice - the second is
`file_change_check` noticing `main.py` changed. Normal.)

## Publishing an update

1. Edit code; bump the file's `@v` tag + `main.py` `__version__`.
2. Bump `version` in `version.json` (and its `files` list if needed).
3. `git commit` + `git push`.
4. Device ▸ **Check for update** ▸ **Update now**.

## Notes

- `config.json` is not in `version.json`'s file list, so OTA never overwrites the
  device's settings.
- Node-RED must be running for OTA to work (it's the proxy).
- A broken `main.py` pushed via OTA could boot-loop (no rollback yet). Test risky
  changes on a spare board.
