# @v 2.1.0 | 2026-07-19 | GitHub OTA updater (private repo via API + token)
try:
    import urequests as requests
except ImportError:
    import requests

import os
import json
import time
import machine
import gc

__version__ = "2.1.0"
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
    """Pulls updated files from a PRIVATE GitHub repo via the REST API.

    Reads config.ota: repo ('owner/name'), branch, token, manifest, auto,
    check_interval. Uses the contents endpoint with an Authorization header so
    private repos work, and does gc.collect() + one-file-at-a-time downloads to
    survive the TLS memory pressure on the ESP32. Optional pause/resume hooks
    stop the onboard web server from competing during the download.
    """

    def __init__(self, config, current_version):
        ota = config.get("ota", {})
        self.enabled = ota.get("enabled", False)
        self.auto = ota.get("auto", False)
        self.repo = ota.get("repo", "")          # "owner/name"
        self.branch = ota.get("branch", "main")
        self.token = ota.get("token", "")
        self.manifest = ota.get("manifest", "version.json")
        self.interval = ota.get("check_interval", 86400)
        self.timeout = ota.get("timeout", 20)
        self.current_version = current_version
        self._last_check = 0

    def _headers(self):
        h = {"User-Agent": "esp32-ota", "Accept": "application/vnd.github.raw"}
        if self.token:
            h["Authorization"] = "token " + self.token
        return h

    def _url(self, path):
        return "https://api.github.com/repos/%s/contents/%s?ref=%s" % (
            self.repo, path, self.branch)

    def _get(self, path, feed=None):
        gc.collect()
        if feed:
            feed()
        try:
            return requests.get(self._url(path), headers=self._headers(),
                                timeout=self.timeout)
        except TypeError:
            return requests.get(self._url(path), headers=self._headers())

    def due(self):
        """True only for AUTO checks whose interval has elapsed."""
        if not self.enabled or not self.repo:
            return False
        return self.auto and (time.time() - self._last_check) >= self.interval

    def check(self, feed=None):
        """Fetch the manifest only (no download). Returns a tuple:
        (ok, remote_version, available, error)."""
        if not self.enabled or not self.repo:
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
        """Check GitHub and update if newer. Returns True if an update was
        applied (device reboots and won't return). pause/resume are optional
        callables to stop the web server during the download."""
        if not self.enabled or not self.repo:
            print("[OTA] not configured (enabled/repo)")
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
                print("[OTA] downloading", fn, "(free %d)" % gc.mem_free())
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

    @classmethod
    def print_version(cls):
        print("OTAUpdater module version:", cls.VERSION if hasattr(cls, "VERSION") else __version__)
