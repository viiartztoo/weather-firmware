# @v 1.2.0 | 2026-08-15 | Hardware/WiFi/MQTT managers + TimeSync + EventLog
from machine import Pin, SoftI2C, WDT
import network
import ubinascii
import json
import time
import machine
import ntptime
from umqtt.simple import MQTTClient

__version__ = "1.2.0"
__date__ = "2026-AUG-15"
__author__ = "Rick Jara"

VERBOSE = False

def _dbg(*args):
    """Print only in verbose mode - keeps normal boot output clean."""
    if VERBOSE:
        print(*args)

class ConfigManager:
    """Handles JSON configuration loading"""
    VERSION = "1.0.2"
    DATE = "2026-JAN-06"
    AUTHOR = "Rick Jara"

    @staticmethod
    def load_config(file_path="config.json"):
        """Load and validate configuration file"""
        try:
            with open(file_path, 'r') as f:
                config = json.load(f)
                _dbg(f"[ConfigManager] loaded {file_path}")
                return config
        except OSError:
            raise RuntimeError(f"[ConfigManager] Missing config file: {file_path}")
        except ValueError as e:
            raise RuntimeError(f"[ConfigManager] Invalid JSON in {file_path}: {str(e)}")

    @classmethod
    def print_version(cls):
        print(f"ConfigManager module version: {cls.VERSION}")
        print(f"Date: {cls.DATE}")
        print(f"Author: {cls.AUTHOR}")

class DeviceSetup:
    """Manages hardware pin configuration and I2C"""
    VERSION = "1.2.1"
    DATE = "2026-JAN-06"
    AUTHOR = "Rick Jara"

    SUPPORTED_DEVICES = {
        "ESP32-S2": {"led": 15, "scl": 39, "sda": 37},
        "ESP32": {"led": 2, "scl": 22, "sda": 21},
        "ESP8266": {"led": 2, "scl": 4, "sda": 5}
    }

    def __init__(self, device, init_i2c=False):
        """Initialize hardware for specified device"""
        self.device = device
        self.led = None
        self.scl = None
        self.sda = None
        self.i2c = None

        _dbg(f"[DeviceSetup] init {device}")
        self._validate_device()
        self._setup_pins()

        if init_i2c:
            self.get_i2c()

    def _validate_device(self):
        """Check if device is supported"""
        if self.device not in self.SUPPORTED_DEVICES:
            raise ValueError(
                f"[DeviceSetup] Unsupported device: {self.device}. "
                f"Supported: {list(self.SUPPORTED_DEVICES.keys())}"
            )

    def _setup_pins(self):
        """Configure GPIO pins according to device"""
        cfg = self.SUPPORTED_DEVICES[self.device]
        self.led = Pin(cfg["led"], Pin.OUT)
        self.scl, self.sda = cfg["scl"], cfg["sda"]
        _dbg(f"[DeviceSetup] pins LED{cfg['led']} SCL{cfg['scl']} SDA{cfg['sda']}")

    def get_i2c(self):
        """Initialize and return I2C interface"""
        if not self.i2c:
            print("[DeviceSetup] Initializing I2C...")
            self.i2c = SoftI2C(scl=Pin(self.scl), sda=Pin(self.sda))
        return self.i2c

    def scan_i2c(self):
        """Scan and return I2C device addresses"""
        if not self.i2c:
            self.get_i2c()
        return self.i2c.scan()

    def toggle_led(self, duration_ms=500):
        """Toggle LED with optional duration"""
        self.led.value(not self.led.value())
        if duration_ms > 0:
            time.sleep_ms(duration_ms)
            self.led.value(not self.led.value())

    def blink_led(self, count=3, delay_ms=200):
        """Blink LED specified times"""
        for _ in range(count):
            self.toggle_led(delay_ms)
            if count > 1:
                time.sleep_ms(delay_ms)

    def return_pins(self):
        """Return (led_pin, scl_pin, sda_pin)"""
        return self.led, self.scl, self.sda

    def return_i2c(self):
        """Return (led_pin, i2c_object)"""
        return self.led, self.get_i2c()

    @classmethod
    def print_version(cls):
        print(f"DeviceSetup module version: {cls.VERSION}")
        print(f"Date: {cls.DATE}")
        print(f"Author: {cls.AUTHOR}")

class WiFiManager:
    """Handles WiFi connections with config"""
    VERSION = "1.1.1"
    DATE = "2026-JAN-06"
    AUTHOR = "Rick Jara"

    def __init__(self, config_file="config.json"):
        """Initialize with configuration"""
        _dbg(f"[WiFiManager] init")
        self.config = ConfigManager.load_config(config_file)
        self.wifi = self.config.get("wifi", {}).get("home_network", {})
        self.static_ip = self.config.get("static_ip", {})
        self.wlan = network.WLAN(network.STA_IF)

    def connect(self):
        """Connect to WiFi with optional static IP"""
        if self.wlan.isconnected():
            _dbg("[WiFiManager] already connected")
            return

        self.wlan.active(True)

        if self.static_ip.get("enabled", False):
            self.wlan.ifconfig((
                self.static_ip["ip"],
                self.static_ip["subnet"],
                self.static_ip["gateway"],
                self.static_ip["dns"]
            ))
            _dbg(f"[WiFiManager] static IP {self.static_ip['ip']}")

        _dbg(f"[WiFiManager] connecting to {self.wifi['ssid']}...")
        self.wlan.connect(self.wifi["ssid"], self.wifi["password"])

        max_attempts = self.wifi.get("max_attempts", 10)
        for attempt in range(max_attempts):
            if self.wlan.isconnected():
                print(f"WiFi   : connected  {self.wlan.ifconfig()[0]}")
                return
            time.sleep(1)

        raise RuntimeError(f"[WiFiManager] Failed to connect after {max_attempts} attempts")

    def is_connected(self):
        """Check if WiFi is connected"""
        return self.wlan.isconnected()

    def get_ip(self):
        """Return the IP address of the device"""
        return self.wlan.ifconfig()[0]

    @classmethod
    def print_version(cls):
        print(f"WiFiManager module version: {cls.VERSION}")
        print(f"Date: {cls.DATE}")
        print(f"Author: {cls.AUTHOR}")

class TimeSync:
    """Sets the ESP32 RTC from an NTP server and formats local timestamps.

    NTP gives UTC, so a fixed utc_offset_hours (from config 'time' block) is
    applied when producing local timestamps. Note: a fixed offset does NOT
    auto-adjust for daylight saving - change it in config.json if your zone
    observes DST.
    """
    VERSION = "1.0.0"
    DATE = "2026-JUL-19"
    AUTHOR = "Rick Jara"

    def __init__(self, config):
        cfg = config.get("time", {})
        self.enabled = cfg.get("ntp_enabled", True)

        hosts = cfg.get("ntp_hosts")
        if not hosts:
            hosts = [cfg.get("ntp_host", "pool.ntp.org")]
        self.hosts = hosts
        self.offset = cfg.get("utc_offset_hours", 0)
        self.retries = cfg.get("retries", 3)
        self.synced = False

    def sync(self, feed=None):
        """Fetch time from NTP and set the RTC (UTC). Returns True on success.

        Tries each host in self.hosts in order (e.g. a local AU server first,
        then a global fallback), repeating the whole list up to self.retries
        rounds before giving up.
        """
        if not self.enabled:
            print("[TimeSync] Disabled (config.time.ntp_enabled=false)")
            return False
        for attempt in range(1, self.retries + 1):
            for host in self.hosts:
                if feed:
                    feed()
                try:
                    ntptime.host = host
                except Exception:
                    pass
                try:
                    ntptime.settime()
                    self.synced = True
                    _dbg(f"[TimeSync] RTC set from {host}")
                    return True
                except Exception as e:
                    print(f"[TimeSync] {host} failed (round {attempt}): {e}")
            time.sleep(2)
        print("[TimeSync] Failed on all hosts - clock remains unsynced (timestamps will be wrong)")
        return False

    def local_time(self):
        """time.localtime() shifted by the configured UTC offset."""
        return time.localtime(time.time() + int(self.offset * 3600))

    def timestamp(self):
        """'YYYY-MM-DD HH:MM:SS' in local time."""
        t = self.local_time()
        return f"{t[0]:04d}-{t[1]:02d}-{t[2]:02d} {t[3]:02d}:{t[4]:02d}:{t[5]:02d}"

    @classmethod
    def print_version(cls):
        print(f"TimeSync module version: {cls.VERSION}")
        print(f"Date: {cls.DATE}")
        print(f"Author: {cls.AUTHOR}")

class EventLog:
    """Small persistent ring buffer of notable events, shown on the dashboard.

    Kept deliberately tiny: state changes only (boot, WiFi up/down, MQTT
    up/down, OTA, sensor faults) - never per-reading noise. Survives a reboot
    because it is mirrored to a JSON file, so after an unexpected restart the
    device can still tell you what happened before it.
    """
    VERSION = "1.0.0"
    DATE = "2026-AUG-15"
    AUTHOR = "Rick Jara"

    def __init__(self, path="events.json", limit=40):
        self.path = path
        self.limit = limit
        self.clock = None
        self.events = []
        try:
            with open(self.path) as f:
                loaded = json.load(f)
            if isinstance(loaded, list):
                self.events = loaded[-self.limit:]
        except Exception:
            self.events = []

    def set_clock(self, clock):
        """Attach a TimeSync so later events carry real timestamps."""
        self.clock = clock

    def add(self, level, msg):
        """Record an event. level is 'info', 'warn' or 'error'."""
        ts = ""
        try:
            if self.clock:
                ts = self.clock.timestamp()
        except Exception:
            pass
        self.events.append({"ts": ts, "lvl": level, "msg": str(msg)[:80]})
        if len(self.events) > self.limit:
            self.events = self.events[-self.limit:]
        print("[Event] %s %s %s" % (ts, level.upper(), msg))
        self._save()

    def _save(self):
        try:
            with open(self.path, "w") as f:
                json.dump(self.events, f)
        except Exception as e:
            print("[EventLog] save failed: %s" % e)

    def recent(self, n=12):
        return self.events[-n:][::-1]

    def as_html(self, n=12):
        """Rows for the dashboard table. Colour-coded by level."""
        colour = {"error": "#f2777a", "warn": "#ffcc66", "info": "#9fb3c8"}
        rows = []
        for e in self.recent(n):
            rows.append(
                "<div><span style='color:%s'>%s</span><span>%s</span></div>"
                % (colour.get(e.get("lvl"), "#9fb3c8"),
                   e.get("msg", ""), e.get("ts", "") or "-")
            )
        return "".join(rows) if rows else "<div><span>no events recorded</span><span>-</span></div>"

    @classmethod
    def print_version(cls):
        print(f"EventLog module version: {cls.VERSION}")
        print(f"Date: {cls.DATE}")
        print(f"Author: {cls.AUTHOR}")

class MQTTManager:
    """MQTT with non-blocking reconnect, health counters and a last will.

    Rewritten in 2.0.0 after the device spent 20 days publishing into a dead
    socket. Three faults caused that, all fixed here:

      * publish() failures were only printed, so nothing ever noticed;
      * the recovery path called disconnect(), which switched the WiFi radio
        off - MQTT recovery must never touch the network interface;
      * connect_mqtt() tested self.client.is_connected(), which umqtt.simple
        does not implement, so recovery raised AttributeError instead.

    The connection is now owned by ensure_connected(), which is safe to call
    every loop: it returns immediately when healthy and applies an increasing
    backoff when not, so a dead broker costs one cheap socket attempt every
    few minutes rather than blocking the main loop.
    """
    VERSION = "2.0.0"
    DATE = "2026-AUG-15"
    AUTHOR = "Rick Jara"

    BACKOFF_START_MS = 5000
    BACKOFF_MAX_MS = 300000

    def __init__(self, config_file="config.json", events=None):
        """Initialize with configuration"""
        _dbg("[MQTTManager] init")
        self.config = ConfigManager.load_config(config_file)
        self.mqtt = self.config.get("mqtt", {})
        self.client = None
        self.connected = False
        self.events = events

        self.client_id = (
            self.config.get("device", {}).get("hostname") or
            f"esp32_{ubinascii.hexlify(machine.unique_id()).decode()}"
        )

        base = self.mqtt.get("base_topic", "outdoor_sensor/BME280")
        self.status_topic = self.mqtt.get("status_topic", base + "/status")
        self.keepalive = self.mqtt.get("keepalive", 60)

        self.publish_ok = 0
        self.publish_fail = 0
        self.reconnects = 0
        self.last_ok_ms = None
        self.last_error = ""
        self._backoff_ms = self.BACKOFF_START_MS
        self._next_try_ms = 0

    def _log(self, level, msg):
        if self.events:
            self.events.add(level, msg)
        else:
            print("[MQTTManager] %s" % msg)

    def connect_mqtt(self, raise_on_fail=True):
        """Open the broker connection. Publishes a retained 'online' status.

        The last will means the broker announces 'offline' on this same topic
        if we vanish without saying goodbye - so a watcher can alert on the
        device dropping off, which is exactly what was missing before.
        """
        if self.connected and self.client:
            _dbg("[MQTTManager] already connected")
            return True

        self._close()

        _dbg(f"[MQTTManager] connecting to {self.mqtt.get('server')}...")
        try:
            self.client = MQTTClient(
                client_id=self.client_id,
                server=self.mqtt["server"],
                port=self.mqtt.get("port", 1883),
                user=self.mqtt.get("username"),
                password=self.mqtt.get("password"),
                keepalive=self.keepalive
            )
            self.client.set_last_will(self.status_topic.encode(), b"offline",
                                      retain=True, qos=0)
            self.client.connect()
            self.client.publish(self.status_topic.encode(), b"online",
                                retain=True, qos=0)

            self.connected = True
            self.last_error = ""
            self._backoff_ms = self.BACKOFF_START_MS
            print(f"MQTT   : connected  {self.mqtt.get('server')}")
            return True

        except Exception as e:
            self._close()
            self.last_error = str(e)
            if raise_on_fail:
                raise RuntimeError(f"[MQTTManager] Connection failed: {str(e)}")
            return False

    def ensure_connected(self):
        """Reconnect if needed, without blocking. Call this every loop.

        Returns True when the link is believed good. On failure it schedules
        the next attempt with exponential backoff instead of hammering a
        broker that is down.
        """
        if self.connected and self.client:
            return True

        now = time.ticks_ms()
        if time.ticks_diff(now, self._next_try_ms) < 0:
            return False

        ok = self.connect_mqtt(raise_on_fail=False)
        if ok:
            self.reconnects += 1
            self._log("info", "MQTT reconnected to %s" % self.mqtt.get("server"))
            return True

        self._backoff_ms = min(self._backoff_ms * 2, self.BACKOFF_MAX_MS)
        self._next_try_ms = time.ticks_add(time.ticks_ms(), self._backoff_ms)
        self._log("warn", "MQTT reconnect failed (%s), retry in %ds"
                  % (self.last_error, self._backoff_ms // 1000))
        return False

    def publish(self, topic, message, retain=False, qos=0):
        """Publish, returning True on success. Never raises, never blocks.

        A failure marks the link dead so ensure_connected() rebuilds it on the
        next loop - the old code left self.client in place and kept writing to
        a socket the broker had long since closed.
        """
        if not self.ensure_connected():
            self.publish_fail += 1
            return False

        try:
            self.client.publish(
                topic.encode(),
                str(message).encode(),
                retain=retain,
                qos=qos
            )
            self.publish_ok += 1
            self.last_ok_ms = time.ticks_ms()
            return True

        except Exception as e:
            self.publish_fail += 1
            self.last_error = str(e)
            was_connected = self.connected
            self._close()
            self._next_try_ms = time.ticks_add(time.ticks_ms(), self.BACKOFF_START_MS)
            if was_connected:
                self._log("error", "MQTT publish failed: %s" % e)
            return False

    def ping(self):
        """Poke the broker to detect a half-open socket.

        A TCP connection dropped by a router or a restarted broker can look
        perfectly writable from this end. Publishing alone will not always
        reveal that, so the main loop pings periodically.
        """
        if not (self.connected and self.client):
            return False
        try:
            self.client.ping()
            return True
        except Exception as e:
            self.last_error = str(e)
            self._close()
            self._log("error", "MQTT ping failed: %s" % e)
            return False

    def subscribe(self, topic, callback, qos=0):
        """Subscribe to MQTT topic with callback"""
        if not self.ensure_connected():
            return False
        try:
            self.client.set_callback(callback)
            self.client.subscribe(topic.encode(), qos=qos)
            print(f"[MQTTManager] Subscribed to {topic}")
            return True
        except Exception as e:
            self.last_error = str(e)
            self._close()
            self._log("error", "MQTT subscribe failed: %s" % e)
            return False

    def check_messages(self):
        """Check for incoming MQTT messages"""
        if not (self.connected and self.client):
            return
        try:
            self.client.check_msg()
        except Exception as e:
            self.last_error = str(e)
            self._close()
            self._log("error", "MQTT check_msg failed: %s" % e)

    def seconds_since_ok(self):
        """Seconds since the last successful publish, or None if never."""
        if self.last_ok_ms is None:
            return None
        return time.ticks_diff(time.ticks_ms(), self.last_ok_ms) // 1000

    def health(self):
        """Machine-readable state, served at /health for external watchers."""
        return {
            "connected": self.connected,
            "server": self.mqtt.get("server", ""),
            "publish_ok": self.publish_ok,
            "publish_fail": self.publish_fail,
            "reconnects": self.reconnects,
            "seconds_since_ok": self.seconds_since_ok(),
            "last_error": self.last_error
        }

    def _close(self):
        """Drop the client socket. Deliberately does NOT touch WiFi."""
        if self.client:
            try:
                self.client.disconnect()
            except Exception:
                pass
        self.client = None
        self.connected = False

    def disconnect(self):
        """Cleanly disconnect MQTT only. WiFi is left alone."""
        self._close()
        print("[MQTTManager] Disconnected")

    @classmethod
    def print_version(cls):
        print(f"MQTTManager module version: {cls.VERSION}")
        print(f"Date: {cls.DATE}")
        print(f"Author: {cls.AUTHOR}")

class UptimeTracker:
    VERSION = "1.0.2"
    DATE = "2026-JAN-06"
    AUTHOR = "Rick Jara"

    def __init__(self):
        """Initialize uptime tracker with current time as start reference"""
        self.start_time = time.time()

    def get_uptime(self):
        """Return uptime in seconds (float)"""
        return time.time() - self.start_time

    def get_uptime_string(self):
        """Return formatted uptime string: 'X days, YY hours, ZZ minutes'"""
        uptime = self.get_uptime()
        days = int(uptime // (60 * 60 * 24))
        hours = int((uptime % (60 * 60 * 24)) // (60 * 60))
        minutes = int((uptime % (60 * 60)) // 60)
        return f"{days} days, {hours:02d} hours, {minutes:02d} minutes"

    def get_uptime_string_short(self):
        """Return short formatted uptime string: 'Xd YYh ZZm'"""
        uptime = self.get_uptime()
        days = int(uptime // (60 * 60 * 24))
        hours = int((uptime % (60 * 60 * 24)) // (60 * 60))
        minutes = int((uptime % (60 * 60)) // 60)
        return f"{days}d {hours:02d}h {minutes:02d}m"

    @classmethod
    def print_version(cls):
        print(f"UptimeTracker module version: {cls.VERSION}")
        print(f"Date: {cls.DATE}")
        print(f"Author: {cls.AUTHOR}")

class Heartbeat:
    VERSION = "1.0.1"
    DATE = "2026-JAN-06"
    AUTHOR = "Rick Jara"

    def __init__(self, timeout=30000):
        self.wdt = WDT(timeout=timeout)

    def feed(self):
        try:
            self.wdt.feed()
        except Exception as e:
            print(f"Error feeding heartbeat: {e}")

    def start(self):
        _dbg("[Heartbeat] started")

    def stop(self):
        try:
            self.wdt.deinit()
            print("Heartbeat stopped")
        except Exception as e:
            print(f"Error stopping heartbeat: {e}")

    def __del__(self):
        self.stop()

    @classmethod
    def print_version(cls):
        print(f"Heartbeat module version: {cls.VERSION}")
        print(f"Date: {cls.DATE}")
        print(f"Author: {cls.AUTHOR}")

class ColorPrinter:
    VERSION = "1.0.1"
    DATE = "2026-JAN-06"
    AUTHOR = "Rick Jara"

    def __init__(self):
        self.colors = {
            "red": "\033[91m",
            "green": "\033[92m",
            "yellow": "\033[93m",
            "blue": "\033[94m",
            "light_blue": "\033[96m",
            "purple": "\033[95m",
            "white": "\033[97m",
            "reset": "\033[0m"
        }

    def print_color(self, value, value_color, label=None, label_color="white", crlf=True):
        if label:
            print(f"{self.colors.get(label_color, self.colors['reset'])}{label}{self.colors['reset']} {self.colors.get(value_color, self.colors['reset'])}{value}{self.colors['reset']}", end='')
        else:
            print(f"{self.colors.get(value_color, self.colors['reset'])}{value}{self.colors['reset']}", end='')

        if crlf:
            print()

    def print_color_samples(self):
        for color_name, color_code in self.colors.items():
            if color_name!= "reset":
                print(f"{color_code}{color_name.capitalize()}: {color_name}{self.colors['reset']}")

    def print_version(self):
        print(f"ColorPrinter module version: {self.VERSION}")
        print(f"Date: {self.DATE}")
        print(f"Author: {self.AUTHOR}")

    @classmethod
    def print_version(cls):
        print(f"ColorPrinter module version: {cls.VERSION}")
        print(f"Date: {cls.DATE}")
        print(f"Author: {cls.AUTHOR}")

def print_module_versions():
    classes = [
        ConfigManager,
        DeviceSetup,
        WiFiManager,
        MQTTManager,
        UptimeTracker,
        Heartbeat,
        ColorPrinter
    ]

    print("Module versions:")
    for cls in classes:
        cls.print_version()
        print()

def main():
    print(f"my_hw module version: {__version__}")
    print(f"Date: {__date__}")
    print(f"Author: {__author__}")
    print_module_versions()

if __name__ == "__main__":
    main()
