import time, network, urequests, gc
from machine import Pin, SoftI2C, WDT
import secrets
# ---- config ----
INTERVAL = 60                  # seconds between readings
ADDR = 0x62                  # from i2c.scan()
# ---- hardware ----
i2c = SoftI2C(scl=Pin(22), sda=Pin(21), freq=50000)
# ---- watchdog ----
# If the board ever truly locks up, this restarts it on its own.
# It has to be "fed" regularly; if we go quiet too long, it assumes
# we're frozen and reboots. 60s is comfortably longer than a normal
# reading + send, but short enough to recover from a real hang quickly.
wdt = WDT(timeout=60000)        # milliseconds
def sleep_fed(seconds):
    # A normal sleep, but we tap the watchdog once a second so it
    # doesn't reboot us during the ordinary 10-minute wait.
    for _ in range(seconds):
        wdt.feed()
        time.sleep(1)
# ----CRC-----
def crc8(pair):
    crc = 0xFF
    for b in pair:
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x31) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc

# ----Sensor start-----
def sensor_start():
    i2c.writeto(ADDR, bytes([0x3F, 0x86]))   # stop, in case it's still running
    time.sleep_ms(500)                        # datasheet wait after stop
    i2c.writeto(ADDR, bytes([0x21, 0xAC]))   # start low-power mode: every 30s
# ----CO2,Temp, humidity---
def reading():
    i2c.writeto(ADDR, bytes([0xEC, 0x05]))   # give me the readings
    time.sleep_ms(2)
    d = i2c.readfrom(ADDR, 9)

    for i in (0, 3, 6):
        if crc8(d[i:i+2]) != d[i+2]:
            raise ValueError("CRC mismatch")

    co2  = (d[0] << 8) | d[1]
    traw = (d[3] << 8) | d[4]
    hraw = (d[6] << 8) | d[7]

    temp = -45 + 175 * traw / 65535
    hum  = 100 * hraw / 65535
    return co2, temp, hum
# ---- wifi ----
def wifi_connect():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        return True
    wlan.connect(secrets.WIFI_SSID, secrets.WIFI_PASS)
    for _ in range(20):
        if wlan.isconnected():
            return True
        wdt.feed()             # keep the watchdog happy while we wait to connect
        time.sleep(1)
    return False
# ---- adafruit io ----
def push(feed, value):
    url = "https://io.adafruit.com/api/v2/{}/feeds/{}/data".format(secrets.AIO_USER, feed)
    try:
        r = urequests.post(url,
            headers={"X-AIO-Key": secrets.AIO_KEY, "Content-Type": "application/json"},
            json={"value": value})
        ok = r.status_code < 300
        if not ok:
            print("push rejected:", feed, r.status_code)
        r.close()
        return ok
    except Exception as e:
        print("push failed:", feed, e)
        return False
# ---- main loop ----
print("logger starting")
sensor_start()
sleep_fed(35)        # first low-power result takes ~30s

while True:
    wdt.feed()
    # Read each sensor on its own, so if one fails (loose cable, etc.)
    # we still keep the other one's reading for this cycle.
    co2 = temp = hum =  None
    try:
        co2, temp, hum  = reading()
        print("CO2 %d ppm   %.1f C   %.0f%%RH" % (co2, temp, hum))
    except Exception as e:
        print("Read failed:", e)
    # Send whatever we managed to read.
    try:
        if wifi_connect():
            wdt.feed()
            if co2 is not None:
                push("air-quality-monitor.co2-ind", co2)
            if temp is not None:
                push("air-quality-monitor.temp-ind", temp)
            if hum is not None:
                push("air-quality-monitor.hum-ind", hum)
        else:
            print("no wifi this cycle")
    except Exception as e:
        print("send failed:", e)
    gc.collect()               # tidy up memory so it doesn't get fragmented over days
    sleep_fed(INTERVAL)        # 10-minute wait, feeding the watchdog throughout
