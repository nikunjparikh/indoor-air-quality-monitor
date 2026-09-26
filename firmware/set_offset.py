# One-off script: change the SCD41's built-in heat correction ("temperature offset")
# and save it on the chip. Run once, with main.py NOT running. Not part of the logger.
from machine import Pin, SoftI2C
import time

i2c  = SoftI2C(scl=Pin(22), sda=Pin(21), freq=50000)
ADDR = 0x62
NEW_OFFSET = 1.0          # °C the sensor should subtract (was 4.0 by default)

def crc8(pair):           # same check-byte maths as main.py
    crc = 0xFF
    for b in pair:
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x31) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc

def send(code, wait_ms):
    i2c.writeto(ADDR, bytes([code >> 8, code & 0xFF]))
    time.sleep_ms(wait_ms)

def read_offset():
    send(0x2318, 1)                       # "tell me your current correction"
    d = i2c.readfrom(ADDR, 3)
    if crc8(d[0:2]) != d[2]:
        raise ValueError("CRC mismatch")
    return ((d[0] << 8) | d[1]) * 175 / 65535

if not 0 <= NEW_OFFSET <= 20:
    raise ValueError("offset must be between 0 and 20 C")

send(0x3F86, 500)                         # 1. stop measuring (settings are locked while measuring)
print("before:", read_offset())           # 2. expect ~4.0

raw  = round(NEW_OFFSET * 65535 / 175)    # 3. convert C to the sensor's 0-65535 scale
data = bytes([raw >> 8, raw & 0xFF])
i2c.writeto(ADDR, bytes([0x24, 0x1D]) + data + bytes([crc8(data)]))
time.sleep_ms(1)

after = read_offset()                     # 4. confirm it took
print("after:", after)
if abs(after - NEW_OFFSET) > 0.05:
    raise ValueError("new value didn't stick - NOT saving")

send(0x3615, 800)                         # 5. save to the chip's long-term memory
print("saved - you can restore main.py now")
