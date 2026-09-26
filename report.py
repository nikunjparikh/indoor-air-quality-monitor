"""Render a static, glanceable page from air_monitor.db.

The page is meant to be read from across the room in a few seconds, so it is
ordered like a story: first the answer ("is the air OK right now?"), then the
evidence (the last 24 hours of CO2), then supporting detail (temperature and
humidity), and finally how fresh the data is.

Everything is inlined - the charts are base64 PNGs inside the HTML - so the
page is a single self-contained file that renders on any browser, however old.
"""
import base64, io, sqlite3, time

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

DB = "air_monitor.db"
OUT = "index.html"
TZ = "Asia/Bangkok"
HOURS = 24          # the main chart covers the last day
DAYS = 7            # the one-line summary underneath it covers the last week
BIN = "10min"       # readings are averaged into 10-minute blocks for the charts

# Short names for the Adafruit feeds, so the rest of the code reads naturally.
FEEDS = {
    "air-quality-monitor.co2-ind": "co2",
    "air-quality-monitor.temp-ind": "temp",
    "air-quality-monitor.hum-ind": "hum",
}

# CO2 is measured in ppm - "parts per million": out of every million
# particles of air, how many are CO2. Outdoor air is about 420.
# These are common rules of thumb for stuffiness, not health limits.
CO2_WATCH = 800     # air is getting stale
CO2_HIGH = 1000     # stuffy - open a window or switch on ventilation
OUTDOOR = 420

# Comfort ranges - PLACEHOLDERS, adjust to what feels right in your room.
TEMP_BAND = (23, 27)
HUM_BAND = (40, 60)

STALE_MIN = 15      # the board sends every minute; older than this = something's wrong

# Colour is used only where it carries meaning. Everything else is grey,
# so the eye goes straight to the one thing that matters.
BG = "#111"
GREY = "#8a8a8a"
DIM = "#555"
GREEN = "#5cb85c"
AMBER = "#e0a030"
RED = "#e05a47"

# Throw away readings that are physically impossible (sensor glitches).
SQL = """
SELECT feed, ts, value FROM readings
WHERE ts >= ?
  AND NOT (feed LIKE '%co2-ind'  AND (value < 250 OR value > 10000))
  AND NOT (feed LIKE '%temp-ind' AND (value < 0   OR value > 50))
  AND NOT (feed LIKE '%hum-ind'  AND (value < 0   OR value > 100))
"""

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def stamp(t):
    """A local wall-clock time like '13th Sep 0955 hrs'.

    Absolute, not relative ("5 min ago"), so it stays true however long the
    page sits on the display without being regenerated.
    """
    d = t.day
    suffix = "th" if 11 <= d <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(d % 10, "th")
    return "{}{} {} {:02d}{:02d} hrs".format(d, suffix, MONTHS[t.month - 1], t.hour, t.minute)


def load():
    """Returns (10-minute table for the last week, latest reading per feed, newest time)."""
    conn = sqlite3.connect(DB)
    df = pd.read_sql_query(SQL, conn, params=[int(time.time()) - DAYS * 86400])
    conn.close()
    if df.empty:
        return None, {}, None
    df["feed"] = df["feed"].map(FEEDS)
    # Convert to Bangkok time, then drop the timezone tag so charts label
    # the axis in local time rather than UTC.
    df["t"] = (pd.to_datetime(df["ts"], unit="s", utc=True)
                 .dt.tz_convert(TZ).dt.tz_localize(None))
    newest = df["t"].max()
    # The big numbers show the actual latest reading, not a 10-minute
    # average, so opening a window shows up on the next refresh.
    last = df.sort_values("t").groupby("feed")["value"].last().to_dict()
    wide = df.pivot_table(index="t", columns="feed", values="value").resample(BIN).mean()
    return wide, last, newest


def status(co2, fresh):
    """The one-line answer at the top of the page, and its colour."""
    if co2 is None or not fresh:
        return "No recent reading", GREY
    if co2 >= CO2_HIGH:
        return "Stuffy - open a window", RED
    if co2 >= CO2_WATCH:
        return "Getting stale", AMBER
    return "Air is fresh", GREEN


def hours_above(s, level):
    """Hours spent at or above `level`, counting only blocks that have data."""
    return (s.dropna() >= level).sum() * pd.Timedelta(BIN).total_seconds() / 3600


def co2_headline(s):
    """A sentence that states what the CO2 chart shows, so no one has to work it out."""
    s = s.dropna()
    if s.empty:
        return "No CO2 readings in the last {} hours".format(HOURS)
    peak_t, peak = s.idxmax(), s.max()
    h = hours_above(s, CO2_HIGH)
    if h == 0:
        return "Last {} h: stayed below {:,} ppm (highest {:,.0f} at {:%H:%M})".format(
            HOURS, CO2_HIGH, peak, peak_t)
    return "Last {} h: stuffy for {:.1f} hours, peaking at {:,.0f} ppm around {:%H:%M}".format(
        HOURS, h, peak, peak_t)


def week_line(s):
    """One sentence putting today in the context of the whole week."""
    s = s.dropna()
    if s.empty:
        return ""
    block_h = pd.Timedelta(BIN).total_seconds() / 3600
    if (s >= CO2_HIGH).sum() == 0:
        return "Past {} days: never went above {:,} ppm".format(DAYS, CO2_HIGH)
    # Average per day = total stuffy hours / days actually covered by data,
    # so a partial first day or an outage doesn't drag the average down.
    days_covered = len(s) * block_h / 24
    avg = hours_above(s, CO2_HIGH) / days_covered
    per_day = (s >= CO2_HIGH).resample("1D").sum() * block_h
    worst = per_day.idxmax()
    return "Past {} days: stuffy {:.1f} h a day on average - longest on {:%a %d %b} ({:.1f} h)".format(
        DAYS, avg, worst, per_day.max())


def band_headline(s, name, unit, band, fmt):
    """e.g. 'Temperature 26.4C - inside your 23-27C range all day'."""
    s = s.dropna()
    if s.empty:
        return name
    now = fmt.format(s.iloc[-1]) + unit
    outside = ((s < band[0]) | (s > band[1])).sum() * pd.Timedelta(BIN).total_seconds() / 3600
    rng = "{}-{}{}".format(band[0], band[1], unit)
    if outside == 0:
        return "{} {} - inside {} all day".format(name, now, rng)
    return "{} {} - outside {} for {:.1f} h".format(name, now, rng, outside)


def _style(ax, fig):
    """Strip everything that isn't data: no box, faint gridlines, grey labels."""
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    for side in ("top", "right", "bottom", "left"):
        ax.spines[side].set_visible(False)
    ax.tick_params(colors="#777", labelsize=10, length=0)
    ax.grid(axis="y", color="#2a2a2a", linewidth=0.6)
    ax.xaxis.set_major_locator(mdates.HourLocator(byhour=range(0, 24, 6)))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))


def _png(fig):
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def co2_chart(s):
    """The main chart: grey line, with the stuffy stretches filled in red."""
    fig, ax = plt.subplots(figsize=(9, 2.8), dpi=110)
    _style(ax, fig)
    # Gaps (NaN) are left in on purpose: the line breaks at an outage
    # instead of drawing a misleading straight line across it.
    ax.plot(s.index, s.values, color=GREY, linewidth=2)
    ax.fill_between(s.index, CO2_HIGH, s.values, where=s.values >= CO2_HIGH,
                    interpolate=True, color=RED, alpha=0.55, linewidth=0)
    ax.axhline(CO2_HIGH, color=RED, linestyle="--", linewidth=1)
    # Label the line directly, instead of making the reader look up a legend.
    ax.text(s.index[0], CO2_HIGH, " stuffy above {:,}".format(CO2_HIGH),
            color=RED, fontsize=9, va="bottom")
    ax.axhline(OUTDOOR, color=DIM, linestyle=":", linewidth=1)
    ax.text(s.index[0], OUTDOOR, " outdoor air ~{}".format(OUTDOOR),
            color=DIM, fontsize=9, va="top")
    # A dot marks "now". No number next to it: the big figure at the top
    # of the page already says it, and two slightly different "now" numbers
    # (one averaged, one not) would only confuse.
    tail = s.dropna()
    if not tail.empty:
        ax.plot(tail.index[-1], tail.iloc[-1], "o", color="#eee", markersize=5)
    peak = s.max() if s.notna().any() else CO2_HIGH
    ax.set_ylim(min(OUTDOOR - 70, s.min() - 50), max(CO2_HIGH * 1.2, peak * 1.08))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: "{:,.0f}".format(v)))
    ax.set_ylabel("CO2 (ppm)", color="#777", fontsize=10)
    return _png(fig)


def small_chart(s, band, ylabel):
    """Temperature / humidity: supporting detail, so kept small and grey.

    The shaded strip is the comfort range; the line only matters when it
    leaves the strip.
    """
    fig, ax = plt.subplots(figsize=(4.4, 1.8), dpi=110)
    _style(ax, fig)
    ax.axhspan(band[0], band[1], color="#1f2a1f", linewidth=0)
    ax.plot(s.index, s.values, color=GREY, linewidth=1.8)
    lo = min(band[0], s.min()) - 1
    hi = max(band[1], s.max()) + 1
    ax.set_ylim(lo, hi)
    ax.set_ylabel(ylabel, color="#777", fontsize=9)
    return _png(fig)


def img(png, alt):
    return '<img src="data:image/png;base64,{}" alt="{}">'.format(png, alt)


TEMPLATE = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="900">
<title>Indoor air</title>
<style>
 body{{margin:0;background:{bg};color:#eee;font-family:-apple-system,Helvetica,Arial,sans-serif;
      text-align:center;padding:20px 12px}}
 .status{{font-size:34px;font-weight:700;margin:0 0 10px;color:{scolor}}}
 .row{{display:flex;justify-content:center;gap:44px;margin:0 0 18px;flex-wrap:wrap}}
 .n{{font-size:30px;font-weight:600}}
 .l{{font-size:12px;color:#888;letter-spacing:.06em}}
 h2{{font-size:17px;font-weight:600;margin:10px 0 0}}
 .ctx{{font-size:13px;color:#888;margin:4px 0 0}}
 .pair{{display:flex;justify-content:center;gap:12px;flex-wrap:wrap;margin-top:14px}}
 .pair div{{max-width:490px}}
 h3{{font-size:13px;font-weight:500;color:#aaa;margin:0}}
 img{{max-width:100%;height:auto;margin-top:6px}}
 .age{{margin-top:12px;font-size:13px;color:{acolor}}}
</style></head><body>
<div class="status">{status}</div>
<div class="row">{cells}</div>
{main}
<div class="pair">{pair}</div>
<div class="age">Last reading {age}</div>
</body></html>"""


def main():
    wide, last, newest = load()

    # How old is the newest reading? Decides whether we trust "right now".
    if newest is None:
        mins = None
        age, acolor = "- none in the last {} days".format(DAYS), RED
    else:
        mins = (pd.Timestamp.now(tz=TZ).tz_localize(None) - newest).total_seconds() / 60
        acolor = "#666" if mins < STALE_MIN else AMBER if mins < 1440 else RED
        age = stamp(newest)
    fresh = mins is not None and mins < STALE_MIN

    words, scolor = status(last.get("co2"), fresh)

    # Only CO2 gets a status colour; temperature and humidity stay white.
    cells = ""
    for key, label, fmt in (("co2", "CO2 PPM", "{:,.0f}"),
                            ("temp", "TEMP", "{:.1f}&deg;C"),
                            ("hum", "HUMIDITY", "{:.0f}%")):
        v = last.get(key)
        colour = scolor if key == "co2" else "#eee"
        cells += '<div><div class="n" style="color:{}">{}</div><div class="l">{}</div></div>'.format(
            colour, "-" if v is None else fmt.format(v), label)

    main_html, pair = "", ""
    if wide is not None:
        day = wide[wide.index >= wide.index.max() - pd.Timedelta(hours=HOURS)]
        if "co2" in day and day["co2"].notna().any():
            main_html = "<h2>{}</h2>{}<div class='ctx'>{}</div>".format(
                co2_headline(day["co2"]),
                img(co2_chart(day["co2"]), "CO2, last {} hours".format(HOURS)),
                week_line(wide["co2"]))
        for key, name, unit, band, fmt in (("temp", "Temperature", "&deg;C", TEMP_BAND, "{:.1f}"),
                                           ("hum", "Humidity", "%", HUM_BAND, "{:.0f}")):
            if key in day and day[key].notna().any():
                pair += "<div><h3>{}</h3>{}</div>".format(
                    band_headline(day[key], name, unit, band, fmt),
                    img(small_chart(day[key], band, name), name))

    html = TEMPLATE.format(bg=BG, status=words, scolor=scolor, cells=cells,
                           main=main_html, pair=pair, age=age, acolor=acolor)
    with open(OUT, "w") as f:
        f.write(html)
    print("wrote {} ({:.0f} KB) - {}, last reading {}".format(OUT, len(html) / 1024, words, age))


if __name__ == "__main__":
    main()
