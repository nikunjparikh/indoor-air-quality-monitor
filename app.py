"""Interactive explorer for air_monitor.db  (run with:  streamlit run app.py)

Where report.py answers "is the air OK right now?", this page answers
"what's the pattern, and what should I change?". It's laid out as a story:

  1. The big picture in one sentence (top of page)
  2. What happened over time        -> when was it stuffy?
  3. The daily rhythm               -> at what time of day does it get bad?
  4. Temperature and humidity       -> supporting detail
  5. Data coverage                  -> can I trust all of the above?

Every chart title is a sentence computed from the data, so it states the
takeaway instead of just naming the chart.
"""
import sqlite3, time

import altair as alt
import pandas as pd
import streamlit as st

DB = "air_monitor.db"
TZ = "Asia/Bangkok"

FEEDS = {
    "air-quality-monitor.co2-ind": "co2",
    "air-quality-monitor.temp-ind": "temp",
    "air-quality-monitor.hum-ind": "hum",
}
LABELS = {"co2": "CO2 (ppm)", "temp": "Temperature (C)", "hum": "Humidity (%)"}

# Same thresholds as report.py - keep the two files in step if you change them.
# ppm = parts per million: out of every million particles of air, how many are CO2.
CO2_WATCH = 800     # air getting stale
CO2_HIGH = 1000     # stuffy - ventilate
OUTDOOR = 420       # typical outdoor air
TEMP_BAND = (23, 27)    # PLACEHOLDER comfort ranges - set to what feels right to you
HUM_BAND = (40, 60)

# Colour only where it means something; everything else is grey.
INK   = "#3c3c3c"   # the main line
MUTED = "#b4b4b4"   # context (bands, secondary series)
ALERT = "#c0392b"   # "too much CO2"
BAND  = "#e8f1e8"   # comfort range shading

SQL = """
SELECT feed, ts, value FROM readings
WHERE ts >= ?
  AND NOT (feed LIKE '%co2-ind'  AND (value < 250 OR value > 10000))
  AND NOT (feed LIKE '%temp-ind' AND (value < 0   OR value > 50))
  AND NOT (feed LIKE '%hum-ind'  AND (value < 0   OR value > 100))
"""

st.set_page_config(page_title="Indoor air", layout="wide")
alt.data_transformers.disable_max_rows()


@st.cache_data(ttl=300)
def load(days):
    conn = sqlite3.connect(DB)
    df = pd.read_sql_query(SQL, conn, params=[int(time.time()) - days * 86400])
    conn.close()
    if df.empty:
        return df
    df["feed"] = df["feed"].map(FEEDS)
    # Bangkok time, with the timezone tag dropped so every chart and every
    # "at 04:00" in the text is local time.
    df["t"] = (pd.to_datetime(df["ts"], unit="s", utc=True)
                 .dt.tz_convert(TZ).dt.tz_localize(None))
    return df.pivot_table(index="t", columns="feed", values="value")


# ---------- turning numbers into sentences ----------

def block_hours(freq):
    return pd.Timedelta(freq).total_seconds() / 3600


def hour_span(hours):
    """Longest run of consecutive clock hours, wrapping past midnight.

    e.g. {23, 0, 1, 2, 5} -> (23, 3) meaning 23:00 to 03:00.
    """
    hours = set(hours)
    if not hours:
        return None
    if len(hours) == 24:
        return (0, 0)
    best = None
    for h in hours:
        if (h - 1) % 24 in hours:
            continue                      # not the start of a run
        n = 0
        while (h + n) % 24 in hours:
            n += 1
        if best is None or n > best[1]:
            best = (h, n)
    return (best[0], (best[0] + best[1]) % 24)


def big_picture(co2, freq, days):
    """The single sentence at the top: how often was it stuffy, and when."""
    s = co2.dropna()
    if s.empty:
        return "No CO2 readings in this window."
    above = s >= CO2_HIGH
    if not above.any():
        return "CO2 stayed below {:,} ppm for the whole of the last {} days.".format(CO2_HIGH, days)
    covered_days = len(s) * block_hours(freq) / 24
    per_day = above.sum() * block_hours(freq) / covered_days
    # Which clock hours are stuffy on most days?
    share = above.groupby(s.index.hour).mean()
    span = hour_span(share[share > 0.5].index)
    when = ""
    if span:
        when = ", usually between {:02d}:00 and {:02d}:00".format(*span)
    else:
        when = ", most often around {:02d}:00".format(share.idxmax())
    return "The air was stuffy (above {:,} ppm) for about {:.1f} hours a day{}.".format(
        CO2_HIGH, per_day, when)


def over_time_title(co2):
    daily_peak = co2.resample("1D").max().dropna()
    bad = int((daily_peak >= CO2_HIGH).sum())
    if bad == 0:
        return "CO2 never crossed {:,} ppm".format(CO2_HIGH)
    return "CO2 crossed {:,} ppm on {} of the {} days shown".format(CO2_HIGH, bad, len(daily_peak))


def rhythm_title(profile):
    hi, lo = profile["median"].idxmax(), profile["median"].idxmin()
    return "CO2 typically peaks around {:02d}:00 ({:,.0f} ppm) and is lowest around {:02d}:00".format(
        hi, profile["median"].max(), lo)


def band_title(s, name, unit, band):
    s = s.dropna()
    if s.empty:
        return name
    inside = ((s >= band[0]) & (s <= band[1])).mean() * 100
    return "{} was inside your {}-{}{} comfort range {:.0f}% of the time".format(
        name, band[0], band[1], unit, inside)


# ---------- charts ----------

def _finish(chart, height):
    """Strip non-data ink: no border, no ticks, faint horizontal gridlines only."""
    return (chart.properties(height=height)
                 .configure_view(stroke=None)
                 .configure_axis(domain=False, ticks=False, grid=False,
                                 labelColor="#888", titleColor="#888",
                                 labelFontSize=11, titleFontSize=11, titlePadding=8)
                 .configure_axisY(grid=True, gridColor="#eee", gridDash=[2, 2]))


def _hover(base, col):
    """An invisible layer that shows a vertical line + tooltip under the mouse."""
    hover = alt.selection_point(nearest=True, on="pointerover",
                                fields=["t"], empty=False, clear="pointerout")
    rule = base.mark_rule(color="#bbb").encode(
        opacity=alt.condition(hover, alt.value(0.8), alt.value(0)),
        tooltip=[alt.Tooltip("t:T", title="", format="%a %d %b, %H:%M"),
                 alt.Tooltip(col + ":Q", title=LABELS[col], format=",.0f" if col == "co2" else ".1f")],
    ).add_params(hover)
    return rule


def ref_line(y, color, text, dash=(4, 4)):
    """A horizontal reference line with its label written on it (no legend needed)."""
    d = pd.DataFrame({"y": [y], "label": [text]})
    rule = alt.Chart(d).mark_rule(color=color, strokeDash=list(dash), strokeWidth=1).encode(y="y:Q")
    # Written at the right-hand edge, just above the line.
    label = alt.Chart(d).mark_text(align="right", baseline="bottom", dx=-2, dy=-3,
                                   color=color, fontSize=11).encode(
        x=alt.value("width"), y="y:Q", text="label:N")
    return [rule, label]


def co2_over_time(wide):
    """Grey line; the stretches above the threshold filled in red."""
    d = wide.reset_index()[["t", "co2"]].dropna()
    base = alt.Chart(d).encode(x=alt.X("t:T", title=None))
    y = alt.Y("co2:Q", title=LABELS["co2"], scale=alt.Scale(zero=False))
    # Fill between the threshold and the line, but only where the line is
    # above it (below, 'excess' equals the threshold, so the fill has no height).
    excess = (base.transform_calculate(excess="max(datum.co2, {})".format(CO2_HIGH))
                  .mark_area(color=ALERT, opacity=0.35)
                  .encode(y="excess:Q", y2=alt.datum(CO2_HIGH)))
    line = base.mark_line(color=INK, strokeWidth=1.5).encode(y=y)
    layers = ([excess, line]
              + ref_line(CO2_HIGH, ALERT, "stuffy above {:,}".format(CO2_HIGH))
              + ref_line(OUTDOOR, MUTED, "outdoor air ~{}".format(OUTDOOR), dash=(1, 3))
              + [_hover(base, "co2")])
    return _finish(alt.layer(*layers), 280)


def co2_rhythm(profile):
    """Average day: median line, with a band covering the middle half of days."""
    d = profile.rename_axis("hour").reset_index()
    base = alt.Chart(d).encode(x=alt.X("hour:O", title="Hour of day",
                                       axis=alt.Axis(labelAngle=0, values=list(range(0, 24, 3)))))
    band = base.mark_area(color=MUTED, opacity=0.35).encode(
        y=alt.Y("p25:Q", title=LABELS["co2"], scale=alt.Scale(zero=False)), y2="p75:Q")
    line = base.mark_line(color=INK, strokeWidth=2, point=alt.OverlayMarkDef(color=INK, size=18)).encode(
        y="median:Q",
        tooltip=[alt.Tooltip("hour:O", title="Hour"),
                 alt.Tooltip("median:Q", title="Typical", format=",.0f"),
                 alt.Tooltip("p25:Q", title="Low end", format=",.0f"),
                 alt.Tooltip("p75:Q", title="High end", format=",.0f")])
    return _finish(alt.layer(band, line, *ref_line(CO2_HIGH, ALERT, "stuffy above {:,}".format(CO2_HIGH))), 240)


def comfort_chart(wide, col, band):
    """Supporting detail: grey line over a shaded comfort range."""
    d = wide.reset_index()[["t", col]].dropna()
    base = alt.Chart(d).encode(x=alt.X("t:T", title=None))
    shade = alt.Chart(pd.DataFrame({"lo": [band[0]], "hi": [band[1]]})).mark_rect(
        color=BAND).encode(y="lo:Q", y2="hi:Q")
    line = base.mark_line(color="#777", strokeWidth=1.4).encode(
        y=alt.Y(col + ":Q", title=LABELS[col], scale=alt.Scale(zero=False)))
    return _finish(alt.layer(shade, line, _hover(base, col)), 170)


# ---------- the page ----------

days = st.sidebar.slider("Days", 1, 30, 7)
freq = st.sidebar.select_slider("Smoothing", ["10min", "30min", "1h", "3h"], value="10min",
                                help="Readings are averaged into blocks of this length. "
                                     "Longer blocks = calmer lines, but short spikes get flattened.")

raw = load(days)
if raw.empty:
    st.warning("No readings in this window. Has puller.py run?")
    st.stop()
wide = raw.resample(freq).mean()
st.sidebar.caption("Data from {:%d %b %Y}".format(raw.index.min()))

st.subheader("Indoor air")

# 1. The big picture, in one sentence.
if "co2" in wide:
    st.markdown("### " + big_picture(wide["co2"], freq, days))

# Right-now numbers, each with a word of context so the number means something.
latest = raw.ffill().iloc[-1]
c1, c2, c3 = st.columns(3)
if "co2" in latest and pd.notna(latest["co2"]):
    v = latest["co2"]
    word = "stuffy" if v >= CO2_HIGH else "getting stale" if v >= CO2_WATCH else "fresh"
    c1.metric("CO2 now (ppm)", "{:,.0f}".format(v))
    c1.caption(word)
for col, key, band, unit in ((c2, "temp", TEMP_BAND, "C"), (c3, "hum", HUM_BAND, "%")):
    if key in latest and pd.notna(latest[key]):
        v = latest[key]
        col.metric(LABELS[key].replace(" (", " now ("), "{:.1f}".format(v))
        col.caption("inside comfort range" if band[0] <= v <= band[1]
                    else "outside {}-{}{}".format(band[0], band[1], unit))
st.caption("Last reading {:%a %d %b, %H:%M}".format(raw.index.max()))

# 2. What happened over time.
if "co2" in wide and wide["co2"].notna().any():
    st.markdown("#### " + over_time_title(wide["co2"]))
    st.altair_chart(co2_over_time(wide), use_container_width=True)

# 3. The daily rhythm - the chart that tells you *when* to open a window.
if "co2" in raw:
    co2 = raw["co2"].dropna()
    if co2.index.normalize().nunique() >= 2:
        g = co2.groupby(co2.index.hour)
        profile = pd.DataFrame({"median": g.median(), "p25": g.quantile(0.25), "p75": g.quantile(0.75)})
        st.markdown("#### " + rhythm_title(profile))
        st.caption("Line = the typical value at each hour across these days. "
                   "Shaded band = where the middle half of days fell.")
        st.altair_chart(co2_rhythm(profile), use_container_width=True)
    else:
        st.caption("The daily-rhythm chart appears once there are at least 2 days of data.")

# 4. Temperature and humidity - supporting detail, kept quieter.
for key, name, unit, band in (("temp", "Temperature", "C", TEMP_BAND),
                              ("hum", "Humidity", "%", HUM_BAND)):
    if key in wide and wide[key].notna().any():
        st.markdown("##### " + band_title(wide[key], name, unit, band))
        st.altair_chart(comfort_chart(wide, key, band), use_container_width=True)

# 5. Can I trust this?
with st.expander("Coverage and raw data"):
    st.caption("Readings stored per day - the board sends one a minute, so a full day is 1,440.")
    st.dataframe(raw.resample("1D").count().rename(columns=LABELS))
    st.dataframe(wide.tail(200).round(1).rename(columns=LABELS))
