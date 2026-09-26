# Indoor Air Quality Monitor


A home-built monitor for **CO2, temperature and humidity**, using an ESP32 and a Sensirion SCD41 sensor. Readings go to Adafruit IO, then into a local database with a dashboard on top.

I built this to learn how each part works, not just to end up with a working gadget. So the code is written by hand, including the sensor driver, and heavily commented.

## Hardware


| Part | What it does |
|---|---|
| ESP32 DevKit (38-pin) | The small computer that runs the code, reads the sensor and sends data over Wi-Fi |
| Sensirion SCD41 breakout | Measures CO2 (up to 5,000 ppm), temperature and humidity. It has to be the SCD41: the SCD40 only goes up to 2,000 ppm |
| Half-size breadboard | A temporary stand while the permanent case is being built |
| OLED screen (I2C) | Bought for showing readings on the device. Not wired into the code yet |

**Wiring:** the SCD41 connects over I2C, a two-wire connection where one wire (SCL) keeps time and the other (SDA) carries the data.

- SCL → GPIO 22
- SDA → GPIO 21
- Sensor address: `0x62`

The SCD41 hangs a few cm below the ESP32, so heat from the board rises away from the sensor.

## How it works


```
SCD41 --(I2C)--> ESP32 --(Wi-Fi)--> Adafruit IO --(puller.py)--> air_monitor.db --> report.py / app.py
```

1. **`firmware/main.py`** (MicroPython, on the ESP32):
   - Puts the SCD41 in *low-power mode*, where it takes a fresh measurement every 30 seconds.
   - About once a minute, it reads CO2, temperature and humidity and sends each one to Adafruit IO (feeds `air-quality-monitor.co2-ind`, `.temp-ind`, `.hum-ind`).
   - Every reading carries a check byte (CRC), a small number worked out from the data. If it doesn't match, the reading was garbled on the wire and gets thrown away.
   - A **watchdog** runs throughout: a timer that restarts the board if the code freezes for more than 60 seconds.
2. **`puller.py`** (on the Mac): downloads new readings from Adafruit IO into `air_monitor.db`, a SQLite database file.
   - It picks up from the last reading it saved, fetching in 6-hour chunks. Adafruit's free plan returns at most 1,000 readings per request and keeps 30 days of data.
   - Running it twice never creates duplicates.
3. **`report.py`**: builds `index.html`, a self-contained page you can read at a glance: "is the air OK right now?", then the last 24 hours of CO2, then temperature and humidity.
4. **`app.py`**: a Streamlit dashboard for exploring patterns (stuffy periods, time-of-day rhythm, data coverage). Run it with `streamlit run app.py`.

CO2 guide used in the dashboards: outdoor air is about 420 ppm, 800+ means the air is getting stale, and 1,000+ means it's stuffy and time to ventilate. These are rules of thumb for comfort, not health limits.

## Setup

1. **Firmware settings.** Create `firmware/secrets.py` on the ESP32:
   ```python
   WIFI_SSID = "your-wifi"
   WIFI_PASS = "your-wifi-password"
   AIO_USER  = "your-adafruit-username"
   AIO_KEY   = "your-adafruit-key"
   ```
   Copy `firmware/main.py` to the board as well.
2. **Mac settings.** Copy `config_example.py` to `config.py` and fill in your Adafruit username and key.
3. **Python packages:** `pip install requests pandas matplotlib streamlit altair`
4. **Pull and view:**
   ```
   python3 puller.py
   python3 report.py      # writes index.html
   streamlit run app.py   # interactive dashboard
   ```

`config.py`, `firmware/secrets.py` and the database are excluded from git on purpose.


## Calibration: fixing the temperature reading

**The problem.** In the first week, the readings didn't make sense:

- A room with no AC read 22.6°C at a flat 100% humidity for hours.
- With the AC on, it read as low as 13.9°C, which is below the lowest temperature the AC can even be set to (16°C).

**The cause.** The SCD41 warms itself slightly while measuring, so it subtracts a fixed amount (the *temperature offset*, 4°C out of the box) from every temperature reading. It then works out humidity using that corrected temperature.

In low-power mode, hanging in open air, this sensor barely warms itself, so it was subtracting too much. That made temperature read low and humidity read high.

**How it was measured.** A Garmin watch sat next to the sensor for several hours. Once the room temperature was changing slowly, the SCD41 read about 3°C below the watch.

Sensirion's formula for the right offset is:

```
new offset = sensor reading − true temperature + old offset
           = −3 + 4 = 1°C
```

**The fix: `firmware/set_offset.py`**, run once. It sends the sensor four commands:

1. Stop measuring (`0x3F86`). The sensor won't accept setting changes while it's measuring.
2. Report the current offset (`0x2318`). This read 4.0.
3. Set the new offset (`0x241D`). The value is converted to the sensor's 0–65535 scale (`1.0 × 65535 ÷ 175 ≈ 374`) and sent with its check byte.
4. Save it to the sensor's long-term memory (`0x3615`), so it survives power-off.

It reads the value back before saving and refuses to save if the change didn't stick. `main.py` doesn't change: the sensor simply remembers the new offset.

**How to run it:** the watchdog can't be switched off once main.py has started it, and it would restart the board mid-script.

1. Temporarily rename `main.py` on the board.
2. Reset the board.
3. Run `set_offset.py`.
4. Rename `main.py` back and reset again.

**Caveats:**

- The watch is slow to react and only shows whole degrees, so "1°C" is a good estimate, not a precise one. A proper room thermometer would make it more precise.
- The offset needs redoing once the sensor goes into its permanent case, because a closed box traps heat.


## Correcting old readings: `fix_old_readings.py`

Readings from before the fix were stored with the old offset. The script corrects them in the database:

- **Temperature:** adds exactly 3.0°C (old offset 4.0 minus new 1.0). This comes from the setting change itself, not from the watch, so old and new readings line up.
- **Humidity:** the amount of water in the air didn't change, only the temperature it was measured against. So the script keeps the water amount the same and recalculates the percentage at the corrected temperature (Magnus formula), which is what the sensor now does internally. Example: 22.6°C / 100% becomes 25.6°C / 83.5%.
- **CO2:** unchanged. The offset doesn't affect it.

Usage (the time is when `set_offset.py` was run, Bangkok time):

```
python3 fix_old_readings.py "2026-09-26 21:27"           # dry run, changes nothing
python3 fix_old_readings.py "2026-09-26 21:27" --apply
```

**Safe to rerun.** Every corrected reading is logged in the table `offset_fixed` and skipped next time, so nothing is ever corrected twice. If the database is ever rebuilt from Adafruit, which still holds the original values, run it again.

**Stuck readings.** 225 humidity readings were stuck at 100%: the real value was at or above what the sensor could show. Their corrected value is a lower limit, not a true reading, and they're marked `clipped = 1`.

## Experiment: does the AC's "I-feel" mode work?

In **I-feel** mode, a Gree AC controls the temperature using a thermometer inside the remote, not the one in the AC unit. The remote sends its reading every 10 minutes.

**Setup (26 Sep 2026):**

- AC set to 22°C.
- The SCD41 and a Garmin watch placed right next to the remote.
- AC on from about 14:45 to 18:45.

**What happened:**

- A steady on/off rhythm: about 20 minutes of cooling, then 30–40 minutes of coasting.
- By the watch, cooling restarted at about 22–23°C and stopped at about 20–21°C, averaging 21.6°C. That's roughly ±1°C around the 22°C setting, measured at the remote. This is what you'd expect if I-feel is working.
- The SCD41 swung much further (about 6°C) than the watch (about 3°C). The sensor is tiny and reacts within seconds to blasts of cold air; the watch is heavy and slow to warm or cool.

**Not proven yet.** The AC's own thermometer could produce a similar result if the room air is well mixed. The deciding test: once the cooling has stopped, warm only the remote in your hand for 10–15 minutes. If the cooling comes back on early, the AC is following the remote. Then repeat with I-feel off, when it shouldn't react.

## Known issues and next steps

- **Bad first reading after a restart.** The first reading after each restart comes out about 4°C too high and humidity too low. Plan: have main.py throw away the first reading after start-up.
- **Readings near where people sit run high on CO2.** People breathe out CO2, so a sensor next to your desk shows your own breath. Keep that in mind when choosing where it lives.
- **Permanent case.** A LEGO case is being built. Redo the temperature offset once the sensor is inside it.
- **OLED screen.** The drivers are included (`sh1107.py`, `framebuf2.py`) but not used by the firmware yet.
- **Second-opinion thermometer.** Check the offset against a proper room thermometer.


## Credits


- The OLED drivers `sh1107.py` and `framebuf2.py` are by Peter Lumb ([peter-l5/SH1107](https://github.com/peter-l5/SH1107)), MIT licence.
- The original idea came from the [FeatureNAB/air-sensor](https://github.com/FeatureNAB/air-sensor) project (ESP32 + SEN66). I switched to the SCD41 because CO2 is the priority, and wrote a MicroPython driver instead of using ESPHome.
