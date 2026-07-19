# @v 1.5.2 | 2026-07-19 | App entry: main loop, sensor, MQTT publish, web dashboard, OTA
import tools
tools.crc_check()

import my_hw
from my_hw import ConfigManager, DeviceSetup, WiFiManager, MQTTManager, Heartbeat, UptimeTracker, ColorPrinter, TimeSync
from ota import OTAUpdater
from PiicoDev_BME280 import PiicoDev_BME280
from PiicoDev_Unified import sleep_ms
import json
import time
import socket
import _thread
import gc

__version__ = "1.5.2"
__date__ = "2026-JUL-19"
__author__ = "Rick Jara"

PRODUCTION = True

latest_sensor_data = {
    "temperature": 0.0,
    "humidity": 0.0,
    "pressure": 0.0,
    "timestamp": "",
    "uptime": ""
}
peak_time_active = False
last_mqtt_payload = "{}"  # exact JSON string last published to MQTT
DASHBOARD_HTML = ""       # dashboard.html loaded once at boot (template)
ota_check_requested = False   # web asked to check for an update
ota_apply_requested = False   # web asked to apply the pending update
ota_status = None             # (ok, remote_version, available, error) from last check

class WebServer:
    """Simple HTTP web server for serving webapp and sensor data"""

    def __init__(self, port=80):
        self.port = port
        self.socket = None
        self.paused = False

    def pause(self):
        """Stop servicing requests (frees the GIL/RAM for an OTA download)."""
        self.paused = True

    def resume(self):
        self.paused = False

    def start(self):
        """Start the web server in a separate thread"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind(('0.0.0.0', self.port))
            self.socket.listen(5)
            self.socket.settimeout(0.1)
            _thread.start_new_thread(self._server_loop, ())
        except Exception as e:
            print(f"[WebServer] Failed to start: {e}")

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
            elif path == '/ota':
                self._handle_ota(conn, query)
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
        """Fill the cached dashboard.html template with live values and send."""
        global latest_sensor_data, last_mqtt_payload, DASHBOARD_HTML
        d = latest_sensor_data

        def _f(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return 0.0

        html = DASHBOARD_HTML
        html = html.replace("{{TEMP}}", "%.1f" % _f(d.get("temperature")))
        html = html.replace("{{HUM}}", "%.1f" % _f(d.get("humidity")))
        html = html.replace("{{PRES}}", "%.0f" % _f(d.get("pressure")))
        html = html.replace("{{TS}}", str(d.get("timestamp", "")))
        html = html.replace("{{UPTIME}}", str(d.get("uptime", "")))
        html = html.replace("{{IP}}", str(d.get("ip_address", "")))
        html = html.replace("{{HOST}}", str(d.get("hostname", "")))
        html = html.replace("{{VER}}", __version__)
        html = html.replace("{{RAW}}", last_mqtt_payload)
        response = ("HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                    "Content-Length: %d\r\nConnection: close\r\n\r\n%s") % (len(html), html)
        try:
            conn.send(response.encode('utf-8'))
        except Exception as e:
            print(f"[WebServer] Error serving page: {e}")

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

    global PRODUCTION
    PRODUCTION = config.get("watchdog", {}).get("enabled", True)
    wdt_timeout = config.get("watchdog", {}).get("timeout_ms", 40000)

    # Verbose logging toggle - quiet by default, full detail when enabled.
    my_hw.VERBOSE = config.get("logging", {}).get("verbose", False)

    device_setup = DeviceSetup("ESP32")

    wifi_manager = WiFiManager()
    wifi_manager.connect()

    time_sync = TimeSync(config)
    if time_sync.sync():
        color_printer.print_color(f"Time   : {time_sync.timestamp()}  (UTC{'+' if time_sync.offset >= 0 else ''}{time_sync.offset}h)", "green")
    else:
        color_printer.print_color("Time   : NOT synced - timestamps may be wrong", "yellow")

    mqtt_manager = MQTTManager()
    mqtt_manager.connect_mqtt()

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

    global latest_sensor_data, last_mqtt_payload, DASHBOARD_HTML
    global ota_check_requested, ota_apply_requested, ota_status
    uptime_tracker = UptimeTracker()

    web_server = None
    ws_cfg = config.get("webserver", {})
    if ws_cfg.get("enabled", True):
        # Load the dashboard template once (filled per request in _serve_dashboard).
        try:
            with open("dashboard.html") as f:
                DASHBOARD_HTML = f.read()
        except OSError:
            DASHBOARD_HTML = "<html><body>dashboard.html missing</body></html>"
        ws_port = ws_cfg.get("port", 80)
        web_server = WebServer(port=ws_port)
        web_server.start()
        color_printer.print_color(f"Web    : http://{wifi_manager.get_ip()}:{ws_port}/", "green")
    else:
        color_printer.print_color("Web    : disabled", "yellow")

    if PRODUCTION:
        heartbeat = Heartbeat(timeout=wdt_timeout)
        heartbeat.start()
        color_printer.print_color(f"WDT    : enabled ({wdt_timeout} ms)", "green")

    else:
        color_printer.print_color("WDT    : disabled (dev/debug)", "yellow")

    color_printer.print_color(f"Ready  : Outdoor Sensor v{__version__} ({__date__})", "blue")

    # Startup self-test: one read + MQTT publish before the loop, so the chain
    # is exercised immediately instead of waiting.
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
        try:
            last_mqtt_payload = json.dumps(_data)
            mqtt_manager.publish("outdoor_sensor/BME280/data", last_mqtt_payload)
            color_printer.print_color("MQTT   : published (startup)", "green")
        except Exception as e:
            color_printer.print_color(f"MQTT   : publish error {e}", "yellow")
    except Exception as e:
        color_printer.print_color(f"Read   : sensor error {e}", "yellow")

    while True:
        try:
            if PRODUCTION:
                heartbeat.feed()

            uptime = uptime_tracker.get_uptime_string()
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

            try:
                last_mqtt_payload = json.dumps(data)
                mqtt_manager.publish("outdoor_sensor/BME280/data", last_mqtt_payload)
            except Exception as e:
                color_printer.print_color(f"MQTT publish error: {e}", "red")

            feed_cb = heartbeat.feed if PRODUCTION else None
            _pause = web_server.pause if web_server else None
            _resume = web_server.resume if web_server else None

            # OTA check (manual): fetch manifest only, store result for the page.
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

            # OTA apply: manual "Update now" button, or automatic scheduled update.
            try:
                if ota_apply_requested or ota_updater.due():
                    ota_apply_requested = False
                    color_printer.print_color("OTA    : applying update...", "yellow")
                    ota_updater.check_and_update(feed=feed_cb, pause=_pause, resume=_resume)
            except Exception as e:
                color_printer.print_color(f"OTA apply error: {e}", "red")

            color_printer.print_color("T: ", "white", crlf=False)
            color_printer.print_color(f"{tempC:6.1f} °C", "red", crlf=False)
            color_printer.print_color(", H: ", "white", crlf=False)
            color_printer.print_color(f"{humRH:6.1f} %", "light_blue", crlf=False)
            color_printer.print_color(", P: ", "white", crlf=False)
            color_printer.print_color(f"{pres_hPa:6.0f} hPa", "blue", crlf=False)

            color_printer.print_color(", TS: ", "white", crlf=False)
            color_printer.print_color(f"{timestamp}", "blue")

            if PRODUCTION:
                heartbeat.feed()

            gc.collect()

            # Sleep in 1s chunks so a manual OTA request is picked up quickly.
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
    main()
