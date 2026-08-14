# @v 2.0.0 | 2026-08-15 | Boot: network + WebREPL + OTA rollback (recovery path)

import json
import os
import time

MAX_BOOT_TRIES = 3
PENDING = "ota_pending.json"

def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False

def _connect_wifi():
    """Join the network using config.json, independent of my_hw."""
    try:
        import network
        with open("config.json") as f:
            cfg = json.load(f)

        wifi = cfg.get("wifi", {}).get("home_network", {})
        if not wifi.get("ssid"):
            return

        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)

        static = cfg.get("static_ip", {})
        if static.get("enabled"):
            wlan.ifconfig((static["ip"], static["subnet"],
                           static["gateway"], static["dns"]))

        if not wlan.isconnected():
            wlan.connect(wifi["ssid"], wifi["password"])
            for _ in range(wifi.get("max_attempts", 10)):
                if wlan.isconnected():
                    break
                time.sleep(1)

        if wlan.isconnected():
            print("boot   : WiFi %s" % wlan.ifconfig()[0])
        else:
            print("boot   : WiFi FAILED - device unreachable until main.py runs")
    except Exception as e:
        print("boot   : WiFi error %s" % e)

def _rollback(files):
    """Restore the previous firmware from the .bak copies ota.py left."""
    restored = 0
    for fn in files:
        bak = fn + ".bak"
        if not _exists(bak):
            continue
        try:
            if _exists(fn):
                os.remove(fn)
            os.rename(bak, fn)
            restored += 1
        except Exception as e:
            print("boot   : rollback of %s failed: %s" % (fn, e))
    print("boot   : ROLLED BACK %d file(s) to the previous firmware" % restored)

def _check_ota():
    """Count boots since an update; roll back if it never came good."""
    if not _exists(PENDING):
        return
    try:
        with open(PENDING) as f:
            pending = json.load(f)
    except Exception:
        try:
            os.remove(PENDING)
        except OSError:
            pass
        return

    tries = pending.get("tries", 0) + 1
    files = pending.get("files", [])
    version = pending.get("version", "?")

    if tries >= MAX_BOOT_TRIES:
        print("boot   : update to %s failed %d times - rolling back" % (version, tries))
        _rollback(files)
        try:
            os.remove(PENDING)
        except OSError:
            pass
        return

    print("boot   : update to %s on trial, boot %d of %d" % (version, tries, MAX_BOOT_TRIES))
    pending["tries"] = tries
    try:
        with open(PENDING, "w") as f:
            json.dump(pending, f)
    except Exception as e:
        print("boot   : could not record boot attempt: %s" % e)

try:
    _check_ota()
except Exception as e:
    print("boot   : OTA check error %s" % e)

try:
    _connect_wifi()
except Exception as e:
    print("boot   : network error %s" % e)

try:
    import webrepl
    webrepl.start()
except Exception as e:
    print("boot   : webrepl error %s" % e)
