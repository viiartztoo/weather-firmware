# @v 1.7.7 | 2026-08-15 | App entry: main loop, sensor, MQTT publish, web dashboard, OTA, settings
import tools
tools.crc_check()

import my_hw
from my_hw import ConfigManager, DeviceSetup, WiFiManager, MQTTManager, Heartbeat, UptimeTracker, ColorPrinter, TimeSync, EventLog
from ota import OTAUpdater
from PiicoDev_BME280 import PiicoDev_BME280
from PiicoDev_Unified import sleep_ms
import json
import time
import socket
import _thread
import gc
import os
import machine

__version__ = "1.7.7"
__date__ = "2026-AUG-15"
__author__ = "Rick Jara"

STALE_AFTER_S = 120

PRODUCTION = True

latest_sensor_data = {
    "temperature": 0.0,
    "humidity": 0.0,
    "pressure": 0.0,
    "timestamp": "",
    "uptime": ""
}
peak_time_active = False
last_mqtt_payload = "{}"
DASHBOARD_HTML = ""
ota_check_requested = False
ota_apply_requested = False
ota_status = None
CONFIG = None
EVENTS = None
MQTT = None
WIFI = None

def _mem_text():
    """Free heap, and the largest block that can actually be allocated.

    The second number is the one that matters. An OTA reads a whole source
    file into RAM, so a heap with 60 KB free but badly fragmented can still
    fail an update. Watch this trend to decide whether MicroPython is running
    out of room on this board.
    """
    try:
        gc.collect()
        free = gc.mem_free()
        alloc = gc.mem_alloc()
        return "%d KB free / %d KB used" % (free // 1024, alloc // 1024)
    except Exception:
        return "unknown"

def _read_ota_error():
    """The reason the last OTA attempt failed, if any. ota.py writes this to
    flash because the device has no serial console in the field."""
    try:
        with open("ota_error.txt") as f:
            return f.read()
    except Exception:
        return ""

def _commit_ota():
    """Accept the running firmware and discard the rollback copies.

    Called only after a successful publish - "it booted" is too weak a test,
    since the fault this release fixes was a device that booted perfectly and
    published nothing. An update that breaks connectivity therefore fails to
    commit, and boot.py restores the previous build on the third boot.
    """
    global EVENTS
    try:
        with open("ota_pending.json") as f:
            pending = json.load(f)
    except Exception:
        return

    for fn in pending.get("files", []):
        try:
            os.remove(fn + ".bak")
        except OSError:
            pass
    try:
        os.remove("ota_pending.json")
    except OSError:
        pass

    msg = "Update to v%s confirmed working" % pending.get("version", __version__)
    if EVENTS:
        EVENTS.add("info", msg)
    else:
        print("[OTA] %s" % msg)

def _banner_html():
    """The one thing missing in 1.6.0: a page that admits when it is lying.

    The readings on the dashboard are always fresh - the sensor is read locally
    every cycle. What can silently fail is getting them off the device, so the
    banner reports the state of the *link*, not the sensor.
    """
    global MQTT, WIFI

    def _led(state, msg):
        return ("<div class='status'><span class='led %s'></span>"
                "<span>%s</span></div>" % (state, msg))

    if WIFI and not WIFI.is_connected():
        return _led("err", "WiFi disconnected &mdash; nothing is reaching the broker")
    if not MQTT:
        return ""
    secs = MQTT.seconds_since_ok()
    if not MQTT.connected or secs is None:
        return _led("err", "MQTT not connected to %s &mdash; %s"
                    % (MQTT.mqtt.get("server", "?"),
                       MQTT.last_error or "no successful publish yet"))
    if secs > STALE_AFTER_S:
        return _led("warn", "No successful publish for %d s &mdash; cloud data is stale" % secs)
    return _led("ok", "Publishing normally &mdash; last success %d s ago" % secs)

def _mqtt_state_text():
    global MQTT
    if not MQTT:
        return "unknown"
    return ("connected to %s" % MQTT.mqtt.get("server", "?")) if MQTT.connected \
        else ("DISCONNECTED (%s)" % (MQTT.last_error or "no error recorded"))

def _last_pub_text():
    global MQTT
    if not MQTT:
        return "unknown"
    secs = MQTT.seconds_since_ok()
    if secs is None:
        return "never"
    if secs < 120:
        return "%d s ago" % secs
    if secs < 7200:
        return "%d min ago" % (secs // 60)
    return "%d hours ago" % (secs // 3600)

def _short_uptime(s):
    """'0 days, 04 hours, 12 minutes' -> '0d04h12m', to fit one console line."""
    try:
        p = s.replace(",", "").split()
        return "%sd%sh%sm" % (p[0], p[2], p[4])
    except Exception:
        return s

def _counts_text():
    global MQTT
    if not MQTT:
        return "-"
    return "%d ok / %d failed / %d reconnects" % (
        MQTT.publish_ok, MQTT.publish_fail, MQTT.reconnects)

def _wifi_text():
    global WIFI
    if not WIFI:
        return "unknown"
    if not WIFI.is_connected():
        return "DISCONNECTED"
    try:
        return "connected (RSSI %d dBm)" % WIFI.wlan.status('rssi')
    except Exception:
        return "connected"

class WebServer:
    """Simple HTTP web server for serving webapp and sensor data"""

    def __init__(self, port=80):
        self.port = port
        self.socket = None
        self.paused = False
        self.running = False

    def pause(self):
        """Stop servicing requests (frees the GIL/RAM for an OTA download)."""
        self.paused = True

    def resume(self):
        self.paused = False

    def start(self):
        """Bind the port and run the accept loop in its own thread.

        Returns True on success. Starting a thread needs a contiguous stack
        allocation, which on a heap this tight fails on some boots and not
        others - so failure must leave self.socket as None and be retryable,
        rather than silently leaving the device with no web interface until
        someone climbs up to it.
        """
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('0.0.0.0', self.port))
            s.listen(5)
            s.settimeout(0.1)
            self.socket = s
            _thread.start_new_thread(self._server_loop, ())
            self.running = True
            return True
        except Exception as e:
            print("[WebServer] Failed to start: %s" % e)
            try:
                if self.socket:
                    self.socket.close()
            except Exception:
                pass
            self.socket = None
            self.running = False
            return False

    def ensure_started(self):
        """Retry a failed start. Called every cycle from the main loop."""
        if self.running and self.socket:
            return True
        gc.collect()
        return self.start()

    def _server_loop(self):
        """Main server loop running in separate thread"""
        while True:
            if self.paused:
                sleep_ms(50)
                continue
            try:
                conn, addr = self.socket.accept()
                conn.settimeout(2.0)

                self._handle_request(conn, addr)
                gc.collect()
            except OSError:

                continue
            except Exception as e:
                print(f"[WebServer] Error accepting connection: {e}")
                sleep_ms(100)
        self.running = False

    def _handle_request(self, conn, addr):
        """Handle individual HTTP request"""
        try:
            request = conn.recv(1024).decode('utf-8')
            if not request:
                conn.close()
                return

            request_line = request.split('\n')[0]
            method_path = request_line.split(' ')
            if len(method_path) < 2:
                conn.close()
                return

            method = method_path[0]
            full_path = method_path[1]
            path = full_path.split('?')[0]
            query = full_path[len(path) + 1:] if '?' in full_path else ''

            if path == '/' or path == '/index.html':
                self._serve_dashboard(conn)
            elif path == '/dashboard' or path == '/dashboard.html':
                self._serve_dashboard(conn)
            elif path == '/update':
                self._serve_json(conn)
            elif path == '/mqtt_data':
                self._serve_mqtt_data(conn)
            elif path == '/data':
                self._serve_json(conn)
            elif path == '/health':
                self._serve_health(conn)
            elif path == '/events':
                self._serve_events(conn)
            elif path == '/ota':
                self._handle_ota(conn, query)
            elif path == '/settings':
                self._handle_settings(conn, query)
            elif path == '/reboot':
                self._handle_reboot(conn)
            elif path == '/peak_update':
                self._serve_peak_status(conn)
            elif path == '/save_peak_data' and method == 'POST':
                self._handle_peak_toggle(conn, request)
            else:
                self._serve_404(conn)

        except Exception as e:
            print(f"[WebServer] Error handling request: {e}")
        finally:
            try:
                conn.close()
            except:
                pass

    def _serve_dashboard(self, conn):
        """Stream dashboard.html, substituting {{TOKENS}} as we go.

        The obvious implementation - a chain of html.replace() calls, then
        encode the finished page - allocates a fresh copy of the whole document
        on every step. At ~4 KB of template plus an event table that is roughly
        seventeen copies per request, and on a heap this fragmented it raises
        MemoryError mid-build. The handler then dies with nothing sent, which
        the browser reports as ERR_EMPTY_RESPONSE.

        Walking the template and sending each segment keeps peak allocation to
        a few hundred bytes, so the page stays available precisely when the
        device is short of memory - which is exactly when you want to look at
        it. No Content-Length, so the response is delimited by Connection:
        close.
        """
        global latest_sensor_data, last_mqtt_payload, DASHBOARD_HTML
        d = latest_sensor_data

        def _f(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return 0.0

        values = {
            "TEMP": "%.1f" % _f(d.get("temperature")),
            "HUM": "%.1f" % _f(d.get("humidity")),
            "PRES": "%.0f" % _f(d.get("pressure")),
            "TS": str(d.get("timestamp", "")),
            "UPTIME": str(d.get("uptime", "")),
            "IP": str(d.get("ip_address", "")),
            "HOST": str(d.get("hostname", "")),
            "VER": __version__,
            "RAW": last_mqtt_payload,
            "BANNER": _banner_html(),
            "MQTT": _mqtt_state_text(),
            "LASTPUB": _last_pub_text(),
            "COUNTS": _counts_text(),
            "WIFI": _wifi_text(),
            "MEM": _mem_text(),
        }

        try:
            conn.send(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                      b"Connection: close\r\n\r\n")
            tmpl = DASHBOARD_HTML
            i = 0
            while True:
                j = tmpl.find("{{", i)
                if j < 0:
                    conn.send(tmpl[i:].encode('utf-8'))
                    break
                if j > i:
                    conn.send(tmpl[i:j].encode('utf-8'))
                k = tmpl.find("}}", j)
                if k < 0:
                    conn.send(tmpl[j:].encode('utf-8'))
                    break
                key = tmpl[j + 2:k]
                if key == "EVENTS":

                    if EVENTS:
                        total = len(EVENTS.recent(20))
                        extra = total - 5 if total > 5 else 0
                        for i, row in enumerate(EVENTS.rows_html(20)):
                            if i == 5 and extra:
                                conn.send(("<details><summary>%d older event%s"
                                           "</summary>" % (extra, "" if extra == 1 else "s")
                                           ).encode('utf-8'))
                            conn.send(row.encode('utf-8'))
                        if extra:
                            conn.send(b"</details>")
                else:
                    val = values.get(key)
                    if val:
                        conn.send(str(val).encode('utf-8'))
                i = k + 2
                gc.collect()
        except Exception as e:
            print("[WebServer] Error serving page: %s" % e)

    def _serve_health(self, conn):
        """Machine-readable health, for Node-RED or any external watcher."""
        global MQTT, WIFI, latest_sensor_data
        payload = {
            "hostname": latest_sensor_data.get("hostname", ""),
            "version": __version__,
            "uptime": latest_sensor_data.get("uptime", ""),
            "timestamp": latest_sensor_data.get("timestamp", ""),
            "wifi_connected": bool(WIFI and WIFI.is_connected()),
            "ip_address": latest_sensor_data.get("ip_address", ""),
            "stale_after_s": STALE_AFTER_S,
            "mqtt": MQTT.health() if MQTT else {},
            "last_ota_error": _read_ota_error(),
            "mem_free": gc.mem_free(),
            "mem_alloc": gc.mem_alloc()
        }
        secs = payload["mqtt"].get("seconds_since_ok") if payload["mqtt"] else None
        payload["healthy"] = bool(
            payload["wifi_connected"]
            and payload["mqtt"].get("connected")
            and secs is not None and secs <= STALE_AFTER_S
        )
        self._send_json(conn, payload)

    def _serve_events(self, conn):
        """The stored event log, newest first."""
        global EVENTS
        self._send_json(conn, EVENTS.recent(40) if EVENTS else [])

    def _send_json(self, conn, obj):
        body = json.dumps(obj)
        response = ("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    "Content-Length: %d\r\nConnection: close\r\n\r\n%s") % (len(body), body)
        try:
            conn.send(response.encode('utf-8'))
        except Exception as e:
            print(f"[WebServer] Error serving json: {e}")

    def _ota_page(self, title, msg, button=None, refresh=None):
        """Build a styled OTA response. button=(label,href), refresh=(url,secs)."""
        meta = ""
        if refresh:
            meta = "<meta http-equiv=refresh content='%d; url=%s'>" % (refresh[1], refresh[0])
        btn = ""
        if button:
            btn = ("<p style='margin-top:20px'><a href='%s' style='display:inline-block;"
                   "background:#1f6feb;color:#fff;text-decoration:none;padding:10px 20px;"
                   "border-radius:8px;font-size:.9rem'>%s</a></p>") % (button[1], button[0])
        body = (
            "<!DOCTYPE html><html><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "%s<title>OTA</title></head>"
            "<body style='font-family:system-ui,sans-serif;background:#0f1720;color:#e5edf5;text-align:center;padding:3em 1em'>"
            "<h2>%s</h2><p style='color:#9fb3c8'>%s</p>%s"
            "<p style='margin-top:10px;font-size:.8rem'><a href='/' style='color:#7d93a8'>&larr; dashboard</a></p>"
            "</body></html>"
        ) % (meta, title, msg, btn)
        return ("HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                "Content-Length: %d\r\nConnection: close\r\n\r\n%s") % (len(body), body)

    def _handle_ota(self, conn, query):
        """OTA page: check -> show result -> apply. Work is done by the main loop."""
        global ota_check_requested, ota_apply_requested, ota_status
        if "apply" in query:
            ota_apply_requested = True
            resp = self._ota_page("Updating",
                                  "Downloading the update. The device will reboot; reconnect in ~30 s.",
                                  refresh=("/", 30))
        elif "check" in query:
            ota_check_requested = True
            ota_status = None
            resp = self._ota_page("Checking for updates", "Contacting GitHub...",
                                  refresh=("/ota", 6))
        elif ota_status is None:
            resp = self._ota_page("Firmware", "Current version: v%s" % __version__,
                                  button=("Check for update", "/ota?check=1"))
        else:
            ok, remote, available, err = ota_status
            if not ok:
                resp = self._ota_page("Check failed", "Error: %s" % err,
                                      button=("Try again", "/ota?check=1"))
            elif available:
                resp = self._ota_page("Update available",
                                      "Current v%s &rarr; available v%s" % (__version__, remote),
                                      button=("Update now", "/ota?apply=1"))
            else:
                resp = self._ota_page("Up to date", "Running the latest: v%s" % __version__,
                                      button=("Check again", "/ota?check=1"))
        try:
            conn.send(resp.encode('utf-8'))
        except Exception as e:
            print(f"[WebServer] Error serving OTA page: {e}")

    def _qs(self, q):
        d = {}
        for part in q.split("&"):
            if not part:
                continue
            if "=" in part:
                k, v = part.split("=", 1)
                d[k] = v
            else:
                d[part] = ""
        return d

    def _send_html(self, conn, body):
        response = ("HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                    "Content-Length: %d\r\nConnection: close\r\n\r\n%s") % (len(body), body)
        try:
            conn.send(response.encode('utf-8'))
        except Exception as e:
            print("[WebServer] Error sending:", e)

    def _save_config(self):
        """Atomically write CONFIG back to config.json. Returns True on success."""
        global CONFIG
        try:
            with open("config.json.new", "w") as f:
                json.dump(CONFIG, f)
            try:
                os.remove("config.json")
            except OSError:
                pass
            os.rename("config.json.new", "config.json")
            return True
        except Exception as e:
            print("[Settings] save failed:", e)
            try:
                os.remove("config.json.new")
            except OSError:
                pass
            return False

    def _notice(self, title, msg, reboot=False):
        meta = "<meta http-equiv=refresh content='30; url=/'>" if reboot else ""
        return (
            "<!DOCTYPE html><html><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>%s<title>%s</title></head>"
            "<body style='font-family:system-ui,sans-serif;background:#0f1720;color:#e5edf5;text-align:center;padding:3em 1em'>"
            "<h2>%s</h2><p style='color:#9fb3c8'>%s</p></body></html>"
        ) % (meta, title, title, msg)

    def _handle_settings(self, conn, query):
        """Show / apply device settings (watchdog, verbose logging), then reboot."""
        global CONFIG
        wd = CONFIG.get("watchdog", {})
        lg = CONFIG.get("logging", {})
        params = self._qs(query)

        if "apply" in params:
            CONFIG.setdefault("watchdog", {})["enabled"] = ("wd" in params)
            try:
                CONFIG["watchdog"]["timeout_ms"] = int(params.get("to", wd.get("timeout_ms", 40000)))
            except ValueError:
                pass
            CONFIG.setdefault("logging", {})["verbose"] = ("verbose" in params)
            if self._save_config():

                self._send_html(conn, self._notice(
                    "Saved",
                    "Settings written to config.json. They take effect at the "
                    "next <b>power cycle</b> - this board does not come back "
                    "cleanly from a software restart, so it is not rebooting "
                    "itself.", reboot=False))
            else:
                self._send_html(conn, self._notice("Error", "Could not write config.json - no changes applied.", reboot=False))
            return

        wd_checked = "checked" if wd.get("enabled", True) else ""
        v_checked = "checked" if lg.get("verbose", False) else ""
        timeout = wd.get("timeout_ms", 40000)
        body = (
            "<!DOCTYPE html><html><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>Settings</title><style>"
            "body{font-family:system-ui,sans-serif;background:#0f1720;color:#e5edf5;max-width:24em;margin:2em auto;padding:0 1em}"
            "h2{font-size:1.2rem}label{display:block;margin:14px 0}"
            "input[type=number]{width:8em;padding:6px;border-radius:6px;border:1px solid #26333f;background:#0b1118;color:#e5edf5}"
            "button{background:#1f6feb;color:#fff;border:0;padding:10px 18px;border-radius:8px;font-size:.9rem;margin-top:10px}"
            ".reboot{background:#b34747}a{color:#7d93a8}"
            "</style></head><body>"
            "<h2>Device settings</h2>"
            "<form method='get' action='/settings'>"
            "<input type='hidden' name='apply' value='1'>"
            "<label><input type='checkbox' name='wd' %s> Watchdog enabled</label>"
            "<p style='font-size:.75rem;color:#d29922;margin:-6px 0 4px'>Leave off on "
            "this board. Only a physical power cycle brings the network stack up "
            "cleanly, so a watchdog reset turns a hang into an unrecoverable "
            "boot loop.</p>"
            "<label>Watchdog timeout (ms): <input type='number' name='to' value='%s'></label>"
            "<label><input type='checkbox' name='verbose' %s> Verbose logging</label>"
            "<button type='submit'>Save &amp; reboot</button></form>"
            "<form method='get' action='/reboot' style='margin-top:24px'>"
            "<button class='reboot' type='submit'>Reboot now</button></form>"
            "<p style='margin-top:20px;font-size:.8rem'><a href='/'>&larr; dashboard</a></p>"
            "</body></html>"
        ) % (wd_checked, timeout, v_checked)
        self._send_html(conn, body)

    def _handle_reboot(self, conn):

        self._send_html(conn, self._notice(
            "Restarting",
            "Restarting now. <b>Warning:</b> this board often comes back with a "
            "broken network stack after a software restart and may need a "
            "physical power cycle. Reconnect in ~30 s.", reboot=True))
        time.sleep(1)
        machine.deepsleep(500)

    def _serve_json(self, conn):
        """Serve JSON sensor data for /update endpoint"""
        global latest_sensor_data, peak_time_active

        json_data = {
            "room": "Outdoor",
            "alias": "Sensor",
            "monitor": "True",
            "currTemp": f"{latest_sensor_data['temperature']:.1f}",
            "currHum": f"{latest_sensor_data['humidity']:.1f}",
            "loRange": "0.0",
            "hiRange": "50.0",
            "updated": latest_sensor_data['timestamp'],
            "sw_status": "on" if peak_time_active else "off",
            "v": __version__
        }

        json_str = json.dumps(json_data)
        response = f"""HTTP/1.1 200 OK
Content-Type: application/json
Content-Length: {len(json_str)}
Connection: close

{json_str}"""
        conn.send(response.encode('utf-8'))

    def _serve_mqtt_data(self, conn):
        """Serve the exact raw JSON last published to MQTT."""
        global last_mqtt_payload
        json_str = last_mqtt_payload
        response = f"""HTTP/1.1 200 OK
Content-Type: application/json
Content-Length: {len(json_str)}
Connection: close

{json_str}"""
        conn.send(response.encode('utf-8'))

    def _serve_peak_status(self, conn):
        """Serve peak time status"""
        global peak_time_active
        status = "PEAK_ACTIVE" if peak_time_active else "PEAK_NOT_ACTIVE"
        response = f"""HTTP/1.1 200 OK
Content-Type: text/plain
Content-Length: {len(status)}
Connection: close

{status}"""
        conn.send(response.encode('utf-8'))

    def _handle_peak_toggle(self, conn, request):
        """Handle peak time toggle POST request"""
        global peak_time_active
        peak_time_active = not peak_time_active
        response = """HTTP/1.1 200 OK
Content-Type: text/plain
Content-Length: 2
Connection: close

OK"""
        conn.send(response.encode('utf-8'))

    def _serve_404(self, conn):
        """Serve 404 Not Found"""
        response = """HTTP/1.1 404 Not Found
Content-Type: text/plain
Content-Length: 13
Connection: close

404 Not Found"""
        conn.send(response.encode('utf-8'))

def main():
    color_printer = ColorPrinter()
    color_printer.print_color(f"=== Outdoor Sensor v{__version__} booting ===", "blue")

    config_manager = ConfigManager()
    config = config_manager.load_config("config.json")

    global PRODUCTION, CONFIG
    CONFIG = config
    PRODUCTION = config.get("watchdog", {}).get("enabled", True)
    wdt_timeout = config.get("watchdog", {}).get("timeout_ms", 40000)

    my_hw.VERBOSE = config.get("logging", {}).get("verbose", False)

    device_setup = DeviceSetup("ESP32")

    global EVENTS, MQTT, WIFI, DASHBOARD_HTML
    EVENTS = EventLog(limit=config.get("logging", {}).get("event_limit", 40))

    wifi_manager = WiFiManager()
    WIFI = wifi_manager
    wifi_manager.connect()

    web_server = None
    ws_cfg = config.get("webserver", {})
    if ws_cfg.get("enabled", True):
        try:
            with open("dashboard.html") as f:
                DASHBOARD_HTML = f.read()
        except OSError:
            DASHBOARD_HTML = "<html><body>dashboard.html missing</body></html>"
        ws_port = ws_cfg.get("port", 80)
        web_server = WebServer(port=ws_port)
        if web_server.start():
            color_printer.print_color(f"Web    : http://{wifi_manager.get_ip()}:{ws_port}/", "green")
        else:
            color_printer.print_color("Web    : start FAILED - will retry each cycle", "red")
    else:
        color_printer.print_color("Web    : disabled", "yellow")

    time_sync = TimeSync(config)
    if time_sync.sync():
        color_printer.print_color(f"Time   : {time_sync.timestamp()}  (UTC{'+' if time_sync.offset >= 0 else ''}{time_sync.offset}h)", "green")
    else:
        color_printer.print_color("Time   : NOT synced - timestamps may be wrong", "yellow")

    EVENTS.set_clock(time_sync)

    _causes = {1: "power on", 2: "hard reset", 3: "WATCHDOG reset",
               4: "deep sleep wake", 5: "soft reboot"}
    try:
        _cause = _causes.get(machine.reset_cause(), "cause %s" % machine.reset_cause())
    except Exception:
        _cause = "unknown"
    EVENTS.add("info", "Boot v%s (%s)" % (__version__, _cause))

    mqtt_manager = MQTTManager(events=EVENTS)
    MQTT = mqtt_manager

    if mqtt_manager.connect_mqtt(raise_on_fail=False):
        EVENTS.add("info", "MQTT connected to %s" % mqtt_manager.mqtt.get("server"))
    else:
        EVENTS.add("error", "MQTT connect failed at boot: %s" % mqtt_manager.last_error)

    color_printer.print_color("Cloud  : via MQTT -> Node-RED", "green")

    ota_updater = OTAUpdater(config, __version__)
    if ota_updater.enabled:
        _mode = f"auto {ota_updater.interval}s" if ota_updater.auto else "manual (button)"
        color_printer.print_color(f"OTA    : enabled - {_mode}", "green")
    else:
        color_printer.print_color("OTA    : disabled", "yellow")

    led, scl_pin, sda_pin = device_setup.return_pins()

    sensor = None
    for address in [0x77, 0x76]:
        try:
            sensor = PiicoDev_BME280(bus=1, sda=sda_pin, scl=scl_pin, address=address)
            color_printer.print_color(f"Sensor : BME280 @0x{address:02X}", "green")
            break
        except Exception as e:
            if address == 0x76:
                color_printer.print_color("ERROR: Could not connect to BME280", "red")
                color_printer.print_color(f"Check wiring: SDA pin {sda_pin}, SCL pin {scl_pin}", "red")
                raise RuntimeError(f"BME280 initialization failed: {str(e)}")

    if sensor is None:
        raise RuntimeError("Failed to initialize BME280 sensor")

    global latest_sensor_data, last_mqtt_payload
    global ota_check_requested, ota_apply_requested, ota_status
    uptime_tracker = UptimeTracker()

    if PRODUCTION:
        heartbeat = Heartbeat(timeout=wdt_timeout)
        heartbeat.start()
        color_printer.print_color(f"WDT    : enabled ({wdt_timeout} ms)", "yellow")
        color_printer.print_color(
            "WDT    : WARNING - a watchdog reset on this board returns with a "
            "broken network stack and loops. Recommend disabling.", "red")

    else:
        color_printer.print_color("WDT    : disabled (dev/debug)", "yellow")

    color_printer.print_color(f"Ready  : Outdoor Sensor v{__version__} ({__date__})", "blue")

    try:
        _tC, _pPa, _hRH = sensor.values()
        _pres = _pPa / 100
        _ts = time_sync.timestamp()
        _uptime = uptime_tracker.get_uptime_string()
        _host = config.get("device", {}).get("hostname", "")
        latest_sensor_data = {
            "temperature": _tC, "humidity": _hRH, "pressure": _pres,
            "timestamp": _ts, "uptime": _uptime,
            "ip_address": wifi_manager.get_ip(), "hostname": _host
        }
        color_printer.print_color(f"Read   : {_tC:.1f} C  {_hRH:.1f} %  {_pres:.0f} hPa", "green")

        _data = {
            "temperature": f"{_tC}", "pressure": f"{_pres}", "humidity": f"{_hRH}",
            "uptime": _uptime, "ip_address": wifi_manager.get_ip(),
            "hostname": _host, "timestamp": _ts,
            "version": __version__, "version_date": __date__
        }
        last_mqtt_payload = json.dumps(_data)
        if mqtt_manager.publish("outdoor_sensor/BME280/data", last_mqtt_payload):
            color_printer.print_color("MQTT   : published (startup)", "green")
        else:
            color_printer.print_color("MQTT   : startup publish failed - will retry", "yellow")
    except Exception as e:
        color_printer.print_color(f"Read   : sensor error {e}", "yellow")

    wifi_ok = True
    publish_ok_last = True
    cycle = 0

    while True:
        try:
            if PRODUCTION:
                heartbeat.feed()

            uptime = uptime_tracker.get_uptime_string()
            if my_hw.VERBOSE:
                color_printer.print_color("Uptime: ", "yellow", crlf=False)
                color_printer.print_color(f"{uptime}", "blue")

            try:
                tempC, presPa, humRH = sensor.values()
                pres_hPa = presPa / 100
            except Exception as e:
                color_printer.print_color(f"Sensor read error: {e}", "red")

                tempC = latest_sensor_data.get("temperature", 0.0)
                humRH = latest_sensor_data.get("humidity", 0.0)
                pres_hPa = latest_sensor_data.get("pressure", 0.0)

            timestamp = time_sync.timestamp()

            latest_sensor_data = {
                "temperature": tempC,
                "humidity": humRH,
                "pressure": pres_hPa,
                "timestamp": timestamp,
                "uptime": uptime,
                "ip_address": wifi_manager.get_ip(),
                "hostname": config.get("device", {}).get("hostname", "")
            }

            data = {
                "temperature":f"{tempC}",
                "pressure": f"{pres_hPa}",
                "humidity": f"{humRH}",
                "uptime": uptime,
                "ip_address": wifi_manager.get_ip(),
                "hostname": config.get("device", {}).get("hostname"),
                "timestamp": timestamp,
                "version": __version__,
                "version_date": __date__
            }

            if not wifi_manager.is_connected():
                if wifi_ok:
                    EVENTS.add("error", "WiFi lost - reconnecting")
                    wifi_ok = False
                try:
                    wifi_manager.connect()
                    wifi_ok = True
                    EVENTS.add("info", "WiFi reconnected as %s" % wifi_manager.get_ip())
                    mqtt_manager.disconnect()
                except Exception as e:
                    color_printer.print_color(f"WiFi reconnect failed: {e}", "red")
            elif not wifi_ok:
                wifi_ok = True
                EVENTS.add("info", "WiFi back as %s" % wifi_manager.get_ip())

            last_mqtt_payload = json.dumps(data)
            published = mqtt_manager.publish("outdoor_sensor/BME280/data", last_mqtt_payload)
            if published:
                if not publish_ok_last:
                    EVENTS.add("info", "Publishing resumed")
                publish_ok_last = True

                _commit_ota()
            else:
                if publish_ok_last:
                    color_printer.print_color("MQTT   : publish failed", "red")
                publish_ok_last = False

            cycle += 1
            if cycle % 10 == 0 and mqtt_manager.connected:
                mqtt_manager.ping()

            if web_server and not web_server.running:
                if web_server.ensure_started():
                    EVENTS.add("info", "Web server started on retry")
                    color_printer.print_color("Web    : started on retry", "green")
                elif cycle % 10 == 1:
                    color_printer.print_color("Web    : still down - retrying", "yellow")

            feed_cb = heartbeat.feed if PRODUCTION else None
            _pause = web_server.pause if web_server else None
            _resume = web_server.resume if web_server else None

            try:
                if ota_check_requested:
                    ota_check_requested = False
                    color_printer.print_color("OTA    : checking...", "yellow")
                    if _pause:
                        _pause()
                    try:
                        ota_status = ota_updater.check(feed=feed_cb)
                    finally:
                        if _resume:
                            _resume()
                    if ota_status[0]:
                        color_printer.print_color("OTA    : %s (local %s, remote %s)" % (
                            "update available" if ota_status[2] else "up to date",
                            __version__, ota_status[1]), "yellow")
                    else:
                        color_printer.print_color("OTA    : check failed - %s" % ota_status[3], "red")
            except Exception as e:
                color_printer.print_color(f"OTA check error: {e}", "red")

            try:
                if ota_apply_requested or ota_updater.due():
                    ota_apply_requested = False
                    color_printer.print_color("OTA    : applying update...", "yellow")
                    ota_updater.check_and_update(feed=feed_cb, pause=_pause, resume=_resume)
            except Exception as e:
                color_printer.print_color(f"OTA apply error: {e}", "red")

            if my_hw.VERBOSE:
                color_printer.print_color("T: ", "white", crlf=False)
                color_printer.print_color(f"{tempC:6.1f} °C", "red", crlf=False)
                color_printer.print_color(", H: ", "white", crlf=False)
                color_printer.print_color(f"{humRH:6.1f} %", "light_blue", crlf=False)
                color_printer.print_color(", P: ", "white", crlf=False)
                color_printer.print_color(f"{pres_hPa:6.0f} hPa", "blue", crlf=False)
                color_printer.print_color(", TS: ", "white", crlf=False)
                color_printer.print_color(f"{timestamp}", "blue")

            try:
                _rssi = wifi_manager.wlan.status('rssi')
            except Exception:
                _rssi = 0
            _ok = mqtt_manager.connected and published
            color_printer.print_color(
                "%s  %5.1fC %5.1f%% %6.0fhPa | mqtt %s %d/%d/%d | wifi %d | %dKB | web %s | up %s" % (
                    timestamp[11:],
                    tempC, humRH, pres_hPa,
                    "OK " if mqtt_manager.connected else "DOWN",
                    mqtt_manager.publish_ok, mqtt_manager.publish_fail,
                    mqtt_manager.reconnects,
                    _rssi,
                    gc.mem_free() // 1024,
                    "up" if (web_server and web_server.running) else "DOWN",
                    _short_uptime(uptime)),
                "green" if _ok else "red")

            if PRODUCTION:
                heartbeat.feed()

            gc.collect()

            for _ in range(30):
                if ota_check_requested or ota_apply_requested:
                    break
                sleep_ms(1000)
            tools.file_change_check()

        except KeyboardInterrupt:

            color_printer.print_color("Shutting down...", "yellow")
            if PRODUCTION:
                heartbeat.stop()
            break
        except Exception as e:

            color_printer.print_color(f"Unexpected error in main loop: {e}", "red")
            if PRODUCTION:
                heartbeat.feed()
            sleep_ms(5000)
if __name__ == "__main__":

    try:
        main()
    except KeyboardInterrupt:
        print("Stopped at the REPL by Ctrl+C - not rebooting")
    except Exception as e:
        print("FATAL: %s" % e)
        try:
            import sys
            sys.print_exception(e)
        except Exception:
            pass
        try:
            with open("events.json") as f:
                _ev = json.load(f)
        except Exception:
            _ev = []
        _ev.append({"ts": "", "lvl": "error", "msg": "FATAL %s" % str(e)[:60]})
        try:
            with open("events.json", "w") as f:
                json.dump(_ev[-40:], f)
        except Exception:
            pass

        print("HALTED - not restarting (a software restart would come back with"
              " a broken network stack).")
        print("The device stays on the network: WebREPL on port 8266, and the"
              " dashboard if it was up. Power cycle to recover.")
