"""Conservative, descriptive comparisons; never a fitness or readiness score."""
from datetime import date, timedelta
from math import isfinite
from statistics import median

import numpy as np

from .heatmap import decode, kind


def number(v):
    return isinstance(v, (int, float)) and isfinite(v)


def close(a, b, tolerance):
    return number(a) and number(b) and a > 0 and b > 0 and abs(a / b - 1) <= tolerance


def hr(summary):
    hist = summary.get("hr_hist") or {}
    values = [(float(k), v) for k, v in hist.items() if number(v) and v > 0 and 40 <= float(k) <= 220]
    seconds = sum(v for _, v in values)
    moving = summary.get("moving_time_s")
    if not number(moving) or moving <= 0 or seconds < moving * .8:
        return None
    return sum(k * v for k, v in values) / seconds


def route_points(line):
    if not line:
        return None
    try:
        p = np.asarray(decode(line), dtype=float)
        if len(p) < 2 or not np.isfinite(p).all():
            return None
        # Equal-distance samples preserve traversal order, including out-and-back turns.
        xy = p * np.array([111320, 111320 * np.cos(np.radians(p[0, 0]))])
        d = np.r_[0, np.cumsum(np.linalg.norm(np.diff(xy, axis=0), axis=1))]
        if d[-1] < 1000:
            return None
        return np.column_stack([np.interp(np.linspace(0, d[-1], 64), d, p[:, i]) for i in range(2)])
    except (IndexError, ValueError, TypeError):
        return None


def same_route(a, b):
    if a is None or b is None:
        return False
    delta = (a - b) * np.array([111320, 111320 * np.cos(np.radians(a[0, 0]))])
    distances = np.linalg.norm(delta, axis=1)
    return bool(np.quantile(distances, .9) <= 100 and distances.max() <= 200)


def build(records, today: date):
    """records contain summary, optional cached terrain and GPS. No raw-file reads here."""
    start = (today - timedelta(days=180)).isoformat()
    rows = sorted([r for r in records if start <= r["day"] <= today.isoformat()
                   and kind(r.get("sport")) in {"run", "trail", "hike"}
                   and str(r.get("sport", "")).lower().replace("_", "") not in
                   {"mountaineering", "skimountaineering", "backcountryski", "backcountryskiing", "skitouring", "snowshoe", "snowshoeing"}],
                  key=lambda r: (r["day"], r["id"]), reverse=True)
    points = {r["id"]: route_points(r.get("line")) for r in rows}
    signals = []
    for metric, title, unit in [("route", "Repeat-route pace", "min/mi"),
                                ("climb", "Climbing speed", "ft/h"),
                                ("durability", "Late-run slowdown", "%")]:
        candidates = []
        for r in rows:
            s, t = r["summary"], r.get("terrain") or {}
            h = hr(s)
            if h is None:
                continue
            if metric == "route" and points[r["id"]] is not None and s.get("distance_mi", 0) > 0:
                candidates.append((r, s["moving_time_s"] / 60 / s["distance_mi"], h, None))
            elif metric == "climb":
                for b in t.get("bands", []):
                    if b["kind"] == "climb" and b.get("time_s", 0) >= 600 and number(b.get("avg_hr")) and 40 <= b["avg_hr"] <= 220 and number(b.get("vert_rate_ft_h")) and b["vert_rate_ft_h"] > 0:
                        candidates.append((r, b["vert_rate_ft_h"], b["avg_hr"], b))
            elif metric == "durability":
                d = t.get("durability") or {}
                if number(d.get("pace_change_pct")):
                    candidates.append((r, d["pace_change_pct"], h, None))
        chosen = None
        best_count = 0
        for current in candidates:
            r, value, effort, band = current
            if r["day"] < (today - timedelta(days=30)).isoformat():
                continue
            matches = []
            for old, v, old_hr, old_band in candidates:
                if old["day"] >= r["day"] or kind(old.get("sport")) != kind(r.get("sport")) or abs(old_hr - effort) > 5:
                    continue
                a, b = r["summary"], old["summary"]
                if metric == "route":
                    fits = close(a.get("distance_mi"), b.get("distance_mi"), .05) and same_route(points[r["id"]], points[old["id"]])
                else:
                    fits = close(a.get("moving_time_s"), b.get("moving_time_s"), .2)
                    if metric == "climb":
                        fits = fits and band["key"] == old_band["key"] and number(band.get("hike_share")) and number(old_band.get("hike_share")) and abs(band["hike_share"] - old_band["hike_share"]) <= .15
                    else:
                        fits = fits and same_route(points[r["id"]], points[old["id"]]) and close(a.get("distance_mi"), b.get("distance_mi"), .05)
                if fits:
                    matches.append((old, v, old_hr))
            # Multiple uploads on a day must not inflate evidence.
            matches = list({x[0]["day"]: x for x in matches}.values())
            best_count = max(best_count, len(matches))
            if len(matches) >= 3:
                baseline = median(v for _, v, _ in matches)
                delta = value - baseline if metric == "durability" else (value / baseline - 1) * 100
                chosen = {"metric": metric, "title": title, "unit": unit, "latest": round(value, 2),
                          "baseline": round(baseline, 2), "change": round(delta, 1),
                          "hr": round(effort), "baseline_hr": round(median(h for _, _, h in matches)),
                          "band": band["label"] if band else None,
                          "evidence": [{"id": x["id"], "day": x["day"], "name": x.get("name") or "Activity", "value": round(v, 2)}
                                       for x, v in [(r, value)] + [(x, v) for x, v, _ in matches]]}
                break
        signals.append(chosen or {"metric": metric, "title": title, "unit": unit, "latest": None,
                                  "matches": best_count})
    return {"since": start, "through": today.isoformat(), "activities": len(rows), "signals": signals}
