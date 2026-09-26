import sqlite3, time
from datetime import datetime, timezone

import requests

import config

DB = "air_monitor.db"
FEEDS = ["air-quality-monitor.co2-ind", "air-quality-monitor.temp-ind", "air-quality-monitor.hum-ind"]
BACKFILL_DAYS = 30  #adafruit free keeps 30 days, so nothing older exists
WINDOW =  6 * 3600   # 6 hours ≈ 360 readings at 1/min, well under the 1,000 cap of Adafruit

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id      TEXT PRIMARY KEY,
    feed    TEXT NOT NULL,
    ts      INTEGER NOT NULL,
    value   REAL
);
CREATE INDEX IF NOT EXISTS idx_feed_ts ON readings(feed,ts);
"""

def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def fetch(feed, start_ts, end_ts):
    url = "https://io.adafruit.com/api/v2/{}/feeds/{}/data".format(config.AIO_USER, feed)
    r = requests.get(
        url,
        headers={"X-AIO-Key": config.AIO_KEY},
        params={"start_time": iso(start_ts), "end_time": iso(end_ts), "limit": 1000},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()

def to_rows(feed, records):
    rows = []
    for rec in records:
        try:
            dt = datetime.fromisoformat(rec["created_at"].replace("Z", "+00:00"))
            rows.append((rec["id"], feed, int(dt.timestamp()), float(rec["value"])))
        except(KeyError, ValueError, TypeError):
            continue
    return rows

def watermark(conn, feed):
    row = conn.execute("SELECT MAX(ts) FROM readings WHERE feed=?", (feed,)).fetchone()
    return row[0]


def sync(conn, feed, now, fetcher=fetch):
    mark = watermark(conn, feed)
    start = (mark - 3600) if mark else (now - BACKFILL_DAYS * 86400)
    added = 0
    t = start
    while t < now:
        end = min(t + WINDOW, now)
        records = fetcher(feed, t, end)
        if len(records) >= 1000:
            print("WARNING: {} hit the 1000 cap — some readings were skipped".format(feed))
        rows = to_rows(feed, records)
        before = conn.total_changes
        conn.executemany("INSERT OR IGNORE INTO readings VALUES (?,?,?,?)", rows)
        added += conn.total_changes - before
        t = end
        time.sleep(0.5)
    conn.commit()
    return added

def main():
    conn = sqlite3.connect(DB)
    conn.executescript(SCHEMA)
    now = int(time.time())
    print("=== run at", datetime.fromtimestamp(now).isoformat(timespec="seconds"))
    for feed in FEEDS:
        n = sync(conn, feed, now)
        total = conn.execute(
            "SELECT COUNT(*) FROM readings WHERE feed=?", (feed,)).fetchone()[0]
        print("{:<12} +{:<5} total {}".format(feed, n, total))
    conn.close()


if __name__ == "__main__":
    main()
