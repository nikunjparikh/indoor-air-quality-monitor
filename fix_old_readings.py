"""
Correct readings taken BEFORE the SCD41's heat correction was changed (4.0 -> 1.0 C).

Temperature: the sensor was subtracting 3.0 C too much, so add exactly 3.0.
Humidity:    the air's actual water content didn't change, only the temperature
             it was measured against. So we keep the water content the same and
             ask "what % is that at the warmer temperature?" (Magnus formula,
             the one Sensirion uses). This is the same thing the sensor now does
             internally.
CO2:         not affected by the heat correction - left alone.

Safe to run more than once: every corrected reading's id is recorded in the
table `offset_fixed`, and those are skipped next time. Run it again after
puller.py brings in any older readings that weren't in the database yet.

Usage:  python3 fix_old_readings.py "2026-09-26 21:10"          (dry run, changes nothing)
        python3 fix_old_readings.py "2026-09-26 21:10" --apply
The time is Bangkok time: the moment set_offset.py was run.
"""
import sqlite3, sys, math
from datetime import datetime, timezone, timedelta

DB = "air_monitor.db"
SHIFT = 3.0                 # old offset 4.0 minus new offset 1.0
PAIR_WINDOW = 60            # seconds: a humidity reading is matched to the temp reading taken with it
BKK = timezone(timedelta(hours=7))

def sat_pressure(t):        # how much water air *can* hold at temperature t (Magnus, relative units)
    return math.exp(17.62 * t / (243.12 + t))

def new_humidity(rh_old, t_old):
    return rh_old * sat_pressure(t_old) / sat_pressure(t_old + SHIFT)

def main():
    fix_ts = int(datetime.strptime(sys.argv[1], "%Y-%m-%d %H:%M").replace(tzinfo=BKK).timestamp())
    apply = "--apply" in sys.argv
    conn = sqlite3.connect(DB)
    # the log of already-corrected readings (created on the first --apply run; empty until then)
    has_log = conn.execute("SELECT 1 FROM sqlite_master WHERE name='offset_fixed'").fetchone()
    if not has_log:
        conn.execute("CREATE TEMP TABLE offset_fixed (id TEXT PRIMARY KEY, clipped INTEGER)")

    q = """SELECT id, ts, value FROM readings WHERE feed LIKE ? AND ts < ?
           AND id NOT IN (SELECT id FROM offset_fixed) ORDER BY ts"""
    temps = conn.execute(q, ("%temp-ind", fix_ts)).fetchall()
    hums  = conn.execute(q, ("%hum-ind",  fix_ts)).fetchall()
    # all old temperature readings (fixed or not) in their ORIGINAL form, for matching humidity
    all_t = conn.execute("""SELECT r.ts, r.value - CASE WHEN f.id IS NULL THEN 0 ELSE ? END
                            FROM readings r LEFT JOIN offset_fixed f ON r.id = f.id
                            WHERE r.feed LIKE '%temp-ind' AND r.ts < ? ORDER BY r.ts""",
                         (SHIFT, fix_ts)).fetchall()

    import bisect
    t_times = [t for t, _ in all_t]
    def matching_temp(ts):
        i = bisect.bisect_left(t_times, ts)
        best = min((j for j in (i - 1, i) if 0 <= j < len(all_t)),
                   key=lambda j: abs(t_times[j] - ts), default=None)
        if best is None or abs(t_times[best] - ts) > PAIR_WINDOW:
            return None
        return all_t[best][1]

    t_updates = [(v + SHIFT, i) for i, ts, v in temps]
    h_updates, clipped, unmatched = [], 0, 0
    for i, ts, v in hums:
        t_old = matching_temp(ts)
        if t_old is None:
            unmatched += 1
            continue
        is_clipped = v >= 99.9          # sensor maxed out at 100% - true value unknown
        clipped += is_clipped
        h_updates.append((round(new_humidity(v, t_old), 2), i, int(is_clipped)))

    print("Fix time: {}  ({} readings before it)".format(sys.argv[1], len(temps) + len(hums)))
    print("Temperature readings to correct: {}".format(len(t_updates)))
    print("Humidity readings to correct:    {}  (of which {} were stuck at 100%)".format(len(h_updates), clipped))
    print("Humidity readings skipped (no temp reading within {}s): {}".format(PAIR_WINDOW, unmatched))
    if temps:
        print("e.g. temp {:.1f} -> {:.1f}".format(temps[-1][2], t_updates[-1][0]))
    if hums and h_updates:
        print("e.g. humidity {:.1f} -> {:.1f}".format(hums[-1][2], h_updates[-1][0]))

    if not apply:
        print("\nDry run - nothing changed. Add --apply to write.")
        return
    with conn:   # all-or-nothing
        if not has_log:
            conn.execute("DROP TABLE temp.offset_fixed")
            conn.execute("CREATE TABLE offset_fixed (id TEXT PRIMARY KEY, clipped INTEGER)")
        conn.executemany("UPDATE readings SET value=? WHERE id=?", t_updates)
        conn.executemany("INSERT INTO offset_fixed VALUES (?, 0)", [(i,) for _, i in t_updates])
        conn.executemany("UPDATE readings SET value=? WHERE id=?", [(v, i) for v, i, _ in h_updates])
        conn.executemany("INSERT INTO offset_fixed VALUES (?, ?)", [(i, c) for _, i, c in h_updates])
    print("\nApplied.")

if __name__ == "__main__":
    main()
