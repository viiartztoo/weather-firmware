# @v 3.0.0 | 2026-07-19 | OTA updater over plain HTTP (via a LAN proxy, e.g. Node-RED)
try:
    import urequests as requests
except ImportError:
    import requests

import os
import json
import time
import machine
import gc

__version__ = "3.0.0"
__date__ = "2026-JUL-19"
__author__ = "Rick Jara"


def _parse_version(v):
    parts = []
    for p in str(v).split("."):
        num = ""
        for ch in p:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


class OTAUpdater:
    """Pulls firmware over plain HTTP from a LAN proxy (no on-device TLS).

    A proxy on the LAN (e.g. Node-RED) fetches the files from the private GitHub
    repo over HTTPS and serves them to the ESP32 over HTTP, so the memory-
    constrained device never does a TLS handshake (which fragments its heap).

    config.ota: base_url ('http://192.168.1.40:1880/firmware/'), manifest, auto,
    check_interval, timeout.
    """

    def __init__(self, config, current_version):
        ota = config.get("ota", {})
        self.enabled = ota.get("enabled", False)
        self.auto = ota.get("auto", False)
        self.base_url = ota.get("base_url", "")
        if self.base_url and not self.base_url.endswith("/"):
            self.base_url += "/"
        self.manifest = ota.get("manifest", "version.json")
        self.interval = ota.get("check_interval", 86400)
        self.timeout = ota.get("timeout", 20)
        self.current_version = current_version
        self._last_check = 0

    def _get(self, path, feed=None):
        gc.collect()
        if feed:
            feed()
        url = self.base_url + path
        try:
            return requests.get(url, timeout=self.timeout)
        except TypeError:
            return requests.get(url)

    def due(self):
        """True only for AUTO checks whose interval has elapsed."""
        if not self.enabled or not self.base_url:
            return False
        return self.auto and (time.time() - self._last_check) >= self.interval

    def check(self, feed=None):
        """Fetch the manifest only (no download). Returns
        (ok, remote_version, available, error)."""
        if not self.enabled or not self.base_url:
            return (False, None, False, "not configured")
        self._last_check = time.time()
        try:
            r = self._get(self.manifest, feed)
            if r.status_code != 200:
                err = "HTTP %s" % r.status_code
                r.close()
                return (False, None, False, err)
            manifest = json.loads(r.text)
            r.close()
        except Exception as e:
            return (False, None, False, str(e))
        remote = manifest.get("version", "0.0.0")
        available = _parse_version(remote) > _parse_version(self.current_version)
        return (True, remote, available, None)

    def check_and_update(self, feed=None, pause=None, resume=None):
        """Check and update if newer. Returns True if an update was applied
        (device reboots and won't return). pause/resume optionally stop the web
        server (less important now that there's no TLS, but harmless)."""
        if not self.enabled or not self.base_url:
            print("[OTA] not configured (enabled/base_url)")
            return False
        self._last_check = time.time()
        if pause:
            try:
                pause()
            except Exception:
                pass
        try:
            return self._run(feed)
        finally:
            if resume:
                try:
                    resume()
                except Exception:
                    pass

    def _run(self, feed):
        # 1) manifest
        try:
            r = self._get(self.manifest, feed)
            if r.status_code != 200:
                print("[OTA] manifest HTTP", r.status_code)
                r.close()
                return False
            manifest = json.loads(r.text)
            r.close()
        except Exception as e:
            print("[OTA] manifest fetch failed:", e)
            return False

        remote = manifest.get("version", "0.0.0")
        files = manifest.get("files", [])
        if _parse_version(remote) <= _parse_version(self.current_version):
            print("[OTA] up to date (local %s, remote %s)" % (self.current_version, remote))
            return False

        print("[OTA] update available: %s -> %s" % (self.current_version, remote))
        if not files:
            print("[OTA] manifest lists no files")
            return False

        # 2) download each file to *.new (all-or-nothing)
        downloaded = []
        try:
            for fn in files:
                if feed:
                    feed()
                gc.collect()
                print("[OTA] downloading", fn)
                r = self._get(fn, feed)
                if r.status_code != 200:
                    raise RuntimeError("%s HTTP %s" % (fn, r.status_code))
                content = r.content
                r.close()
                with open(fn + ".new", "wb") as f:
                    f.write(content)
                content = None
                gc.collect()
                downloaded.append(fn)
        except Exception as e:
            print("[OTA] download failed:", e, "- aborting, no files changed")
            self._cleanup(downloaded)
            return False

        # 3) swap all in, then reboot
        try:
            for fn in downloaded:
                try:
                    os.remove(fn)
                except OSError:
                    pass
                os.rename(fn + ".new", fn)
            print("[OTA] updated to %s - rebooting" % remote)
            time.sleep(1)
            machine.reset()
        except Exception as e:
            print("[OTA] swap failed:", e)
            return False
        return True

    def _cleanup(self, files):
        for fn in files:
            try:
                os.remove(fn + ".new")
            except OSError:
                pass
