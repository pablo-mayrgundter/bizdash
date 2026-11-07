"""Robust spike detection over an hourly series. Mirrors web/src/spikes.js.

Baseline: the median of the same hour of day over the previous ``lookback_days``
days. Spread: the MAD of those samples. A point spikes when its robust z-score
clears ``z`` and it adds at least ``min_extra`` over the baseline. Consecutive
spiking hours are one episode.
"""

from statistics import median

DEFAULTS = {"z": 4.0, "min_extra": 15, "lookback_days": 14}


def _round(x, digits):
    # JavaScript's Math.round(x * 10**d) / 10**d, so both sides agree on ties
    from math import floor
    f = 10 ** digits
    return floor(x * f + 0.5) / f


def robust_scores(series, lookback_days=DEFAULTS["lookback_days"]):
    """series: [(ts 'YYYYMMDDHH', value)] chronological and hourly.
    Returns [{ts, value, baseline, z}]."""
    values = [v for _, v in series]
    out = []
    for i, (ts, v) in enumerate(series):
        samples = []
        j = i - 24
        while j >= 0 and len(samples) < lookback_days:
            samples.append(values[j])
            j -= 24
        if len(samples) < 3:
            samples = values[max(0, i - 24):i]
        if not samples:
            samples = [v]
        base = median(samples)
        mad = median([abs(s - base) for s in samples]) or max(base * 0.15, 1.0)
        out.append({"ts": ts, "value": v, "baseline": _round(base, 1),
                    "z": _round((v - base) / (1.4826 * mad), 2)})
    return out


def find_episodes(scored, z=DEFAULTS["z"], min_extra=DEFAULTS["min_extra"]):
    """Group consecutive spiking hours into episodes."""
    episodes, cur = [], None
    for pt in scored:
        if pt["z"] >= z and pt["value"] - pt["baseline"] >= min_extra:
            if cur is None:
                cur = {"start": pt["ts"], "end": pt["ts"], "peak": pt["value"], "peak_ts": pt["ts"],
                       "peak_z": pt["z"], "baseline": pt["baseline"], "extra": 0.0, "hours": []}
            cur["end"] = pt["ts"]
            cur["hours"].append(pt["ts"])
            cur["extra"] += pt["value"] - pt["baseline"]
            if pt["value"] > cur["peak"]:
                cur.update(peak=pt["value"], peak_ts=pt["ts"], peak_z=pt["z"], baseline=pt["baseline"])
        elif cur:
            episodes.append(cur)
            cur = None
    if cur:
        episodes.append(cur)
    for e in episodes:
        e["extra"] = int(_round(e["extra"], 0))
    return episodes
