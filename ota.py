# @v 4.1.0 | 2026-08-15 | OTA over plain HTTP: streamed download, compile check, backups, rollback
try:
    import urequests as requests
except ImportError:
    import requests

import os
import json
import time
import machine
import gc

__version__ = "4.1.0"
__date__ = "2026-AUG-15"
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

    def _get(self, path, feed=None, headers=None):
        gc.collect()
        if feed:
            feed()
        url = self.base_url + path
        kw = {}
        if headers:
            kw["headers"] = headers
        try:
            return requests.get(url, timeout=self.timeout, **kw)
        except TypeError:
            return requests.get(url, **kw)

    def _download_to(self, path, dest, feed=None):
        """Stream a file to flash in small chunks.

        r.content allocates the whole body as one contiguous block. main.py is
        ~36 KB and this chip's heap is fragmented, so that reliably fails while
        a 9 KB file succeeds - which is exactly how the 1.7.0 update stalled.
        Reading 512 bytes at a time keeps the peak allocation tiny regardless
        of how large the firmware grows.

        'Connection: close' is requested so the socket reaches EOF; the proxy
        otherwise keeps it alive and the read would block until timeout.
        """
        r = self._get(path, feed, headers={"Connection": "close"})
        if r.status_code != 200:
            r.close()
            raise RuntimeError("%s HTTP %s" % (path, r.status_code))

        total = 0
        try:
            with open(dest, "wb") as f:
                while True:
                    if feed:
                        feed()
                    chunk = r.raw.read(512)
                    if not chunk:
                        break
                    f.write(chunk)
                    total += len(chunk)
        finally:
            try:
                r.close()
            except Exception:
                pass
            gc.collect()

        if total == 0:
            raise RuntimeError("%s downloaded 0 bytes" % path)
        return total

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

        downloaded = []
        try:
            for fn in files:
                if feed:
                    feed()
                gc.collect()
                print("[OTA] downloading", fn)
                n = self._download_to(fn, fn + ".new", feed)
                print("[OTA]   %s %d bytes" % (fn, n))
                downloaded.append(fn)
        except Exception as e:
            print("[OTA] download failed:", e, "- aborting, no files changed")
            self._write_error("download failed: %s" % e)
            self._cleanup(downloaded)
            return False

        checked = skipped = 0
        for fn in downloaded:
            if not fn.endswith(".py"):
                continue
            if feed:
                feed()
            gc.collect()
            src = None
            try:
                with open(fn + ".new") as f:
                    src = f.read()
                compile(src, fn, "exec")
                checked += 1
            except SyntaxError as e:
                print("[OTA] %s has a syntax error: %s - aborting, nothing changed" % (fn, e))
                self._write_error("%s syntax error: %s" % (fn, e))
                self._cleanup(downloaded)
                return False
            except Exception as e:

                print("[OTA] %s could not be verified (%s) - continuing" % (fn, e))
                skipped += 1
            finally:
                src = None
                gc.collect()
        print("[OTA] pre-flight: %d verified, %d unverified" % (checked, skipped))

        try:
            for fn in downloaded:
                bak = fn + ".bak"
                try:
                    os.remove(bak)
                except OSError:
                    pass
                try:
                    os.rename(fn, bak)
                except OSError:
                    pass
                os.rename(fn + ".new", fn)
        except Exception as e:
            print("[OTA] swap failed:", e)
            return False

        try:
            with open("ota_pending.json", "w") as f:
                json.dump({"version": remote, "files": downloaded, "tries": 0}, f)
        except Exception as e:
            print("[OTA] could not write pending marker:", e)

        print("[OTA] updated to %s (on trial) - rebooting" % remote)
        time.sleep(1)
        machine.reset()
        return True

    def _write_error(self, msg):
        """Leave the reason on flash - the device has no serial console in the
        field, so a failed update must be able to explain itself afterwards.
        Served by /health from firmware 1.7.0 onwards."""
        try:
            with open("ota_error.txt", "w") as f:
                f.write(str(msg)[:200])
        except Exception:
            pass

    def _cleanup(self, files):
        for fn in files:
            try:
                os.remove(fn + ".new")
            except OSError:
                pass
