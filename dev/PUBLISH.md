# Publishing a firmware update (OTA)

How to release a new version so the device can pull it via OTA.

There are two "manifests":
- **`version.json`** — the OTA manifest the device reads: the bundle `version`
  and the `files` list to download. **This is the one that drives OTA.**
- **`MANIFEST.txt`** — a human-readable table generated from each file's `@v`
  tag. Nice to have, not used by OTA.

## Step-by-step

1. **Edit the code** you want to change.

2. **Bump the changed file's `@v` tag** (top of the file):
   ```
   # @v 1.6.1 | 2026-07-20 | ...
   ```
   If you changed `main.py`, also bump its runtime version to match:
   ```python
   __version__ = "1.6.1"
   ```

3. **Bump the bundle version in `version.json`** — this is what makes the device
   see an update. Also update the `files` list if you added/renamed a file:
   ```json
   {
       "version": "1.6.1",
       "updated": "2026-07-20",
       "files": ["main.py", "my_hw.py", "ota.py", "tools.py", "dashboard.html"]
   }
   ```
   > OTA only downloads files in this list. Never add `config.json` (secrets /
   > per-device settings) or `webrepl_cfg.py`.

4. **Regenerate `MANIFEST.txt`** (optional but tidy):
   ```powershell
   cd "F:\mastershare\SourceCode\MicroPython\Outside_sensor\Production\v1.3.0 - cloud + OTA\dev"
   python gen_manifest.py
   ```

5. **Also update `dev/CHANGELOG.md`** with a line for the new version.

6. **Push to GitHub** (from the firmware folder):
   ```powershell
   cd "F:\mastershare\SourceCode\MicroPython\Outside_sensor\Production\v1.3.0 - cloud + OTA"
   git add -A
   git commit -m "Firmware 1.6.1"
   git push
   ```

7. **Update the device**: open its page → **Check for update** → **Update now**.
   It downloads the files in `version.json` over HTTP (via the Node-RED proxy)
   and reboots into the new version.

## Quick copy-paste (the git part)

```powershell
cd "F:\mastershare\SourceCode\MicroPython\Outside_sensor\Production\v1.3.0 - cloud + OTA"
git add -A
git commit -m "Firmware <new-version>"
git push
```

## Checklist before pushing

- [ ] `main.py` `__version__` **==** `version.json` `version` (the device
      compares these).
- [ ] `version.json` `files` list matches the files you actually changed/need.
- [ ] Secrets are NOT staged: `git status` should never show `config.json` or
      `webrepl_cfg.py` (they're git-ignored).
- [ ] `MANIFEST.txt` regenerated (if you care about the readable list).

## Version numbering (a suggestion)

- Patch (`1.6.0 -> 1.6.1`): small fixes.
- Minor (`1.6.x -> 1.7.0`): new features.
- The comparison is numeric per part, so `1.6.10` > `1.6.2` works correctly.

## Gotchas

- The device must be running firmware **older** than `version.json` for an update
  to appear. Same version = "up to date".
- Node-RED (the OTA proxy) must be running.
- After an update the device may reboot **twice** (the second is
  `file_change_check` noticing `main.py` changed). Normal.
- A broken `main.py` pushed via OTA could boot-loop (no rollback yet) — test
  risky changes on a spare board first.
