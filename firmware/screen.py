# screen.py: everything to do with the screen lives here.
import time
import sh1107
import framebuf2

NIGHT_START = 21          # screen off from 21:00...
NIGHT_END = 6             # ...until 06:00
UTC_OFFSET = 7 * 3600     # seconds to add to the board's clock (step 1)

_d = None                 # the screen; set up once by start()

def start(i2c):
    global _d             # save into the file-wide _d, not a throwaway copy
    _d = sh1107.SH1107_I2C(128, 128, i2c, address=0x3d)
    _d.sleep(False)

def _is_night():
    t = time.localtime(time.time() + UTC_OFFSET)
    if t[0] < 2024:       # clock not set yet: stay on rather than guess
        return False
    return t[3] >= NIGHT_START or t[3] < NIGHT_END   # t[3] is the hour

def _x(text, scale):      # left position that centres the text
    return (128 - len(text) * 8 * scale) // 2

def _big(text, y, scale):
    framebuf2.FrameBuffer.large_text(_d, text, _x(text, scale), y, scale, 1)

def update(co2, temp, hum, pm25=None):
    if _is_night():
        _d.sleep(True)    # panel off: no light, no wear
        return
    _d.sleep(False)
    _d.fill(0)            # blank the picture in memory
    _d.text("CO2 ppm", _x("CO2 ppm", 1), 6, 1)
    c = "{:.0f}".format(co2)
    _big(c, 22, 4 if len(c) <= 4 else 3)
    _d.hline(0, 64, 128, 1)
    _big("{:.0f}C {:.0f}%".format(temp, hum), 72, 2)   # temp + humidity now share a line
    _d.text("PM2.5 ug/m3", _x("PM2.5 ug/m3", 1), 96, 1)
    p = "--" if pm25 is None else "{:.0f}".format(pm25)    # "--" when there's no reading
    _big(p, 108, 2)
    _d.show()             # send the finished picture to the glass
