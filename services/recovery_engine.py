from __future__ import annotations

import math
import statistics
from datetime import datetime, timezone
from typing import Any


def _median(xs: list[float]) -> float | None:
    return float(statistics.median(xs)) if xs else None


def _wilson_lower(successes: int, total: int, z: float = 1.645) -> float:
    if total <= 0:
        return 0.0
    p = successes / total
    denom = 1 + z * z / total
    centre = p + z * z / (2 * total)
    adj = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return max(0.0, (centre - adj) / denom)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _weekly_closes(bars: list[dict[str, Any]]) -> list[tuple[int, float]]:
    """Collapse daily IBKR bars to the last close in each ISO week."""
    buckets: dict[tuple[int, int], tuple[int, float]] = {}
    for b in bars:
        try:
            ts = int(b.get("t") or 0)
            close = float(b.get("c"))
        except Exception:
            continue
        if ts <= 0 or close <= 0:
            continue
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        iso = dt.isocalendar()
        buckets[(iso.year, iso.week)] = (ts, close)
    return [buckets[k] for k in sorted(buckets)]


def _bench_return(bench: list[tuple[int, float]], start_ts: int, end_ts: int) -> float | None:
    if not bench:
        return None
    start = min(bench, key=lambda x: abs(x[0] - start_ts))
    end = min(bench, key=lambda x: abs(x[0] - end_ts))
    if start[1] <= 0:
        return None
    return end[1] / start[1] - 1


def analyse_recovery(stock_bars: list[dict[str, Any]], spy_bars: list[dict[str, Any]], current_price: float) -> dict[str, Any]:
    p = _weekly_closes(stock_bars)
    b = _weekly_closes(spy_bars)
    if len(p) < 80:
        return {"ready": False, "label": "Insufficient", "score": None, "samples": 0, "history": [], "reason": "Less than ~18 months of usable weekly history."}

    current_i = len(p) - 1
    lookback = 16
    current_window = p[max(0, current_i - lookback + 1): current_i + 1]
    current_peak = max(x[1] for x in current_window)
    cp = float(current_price or p[-1][1])
    current_dd = cp / current_peak - 1 if current_peak > 0 else 0.0
    tol = max(0.035, abs(current_dd) * 0.30)
    lo, hi = current_dd - tol, current_dd + tol

    events: list[dict[str, Any]] = []
    last_entry = -999
    # Leave the latest six months out so the reference set cannot contain the current episode.
    stop = max(lookback + 1, len(p) - 26)
    for i in range(lookback, stop):
        if i + 20 >= len(p):
            break
        window = p[i - lookback + 1:i + 1]
        peak_rel = max(range(len(window)), key=lambda j: window[j][1])
        peak_i = i - lookback + 1 + peak_rel
        peak = p[peak_i][1]
        entry = p[i][1]
        if peak <= 0:
            continue
        dd = entry / peak - 1
        if not (lo <= dd <= hi) or peak_i >= i - 1:
            continue
        recent = p[max(0, i - 7):i + 1]
        low = min(x[1] for x in recent)
        bounce = entry / low - 1 if low > 0 else 0.0
        last2 = entry / p[i - 2][1] - 1 if i >= 2 and p[i - 2][1] > 0 else 0.0
        if bounce < 0.015 and last2 < 0.005:
            continue
        if i - last_entry < 10:
            continue

        ret12 = p[i + 12][1] / entry - 1
        bench12 = _bench_return(b, p[i][0], p[i + 12][0])
        excess = ret12 - bench12 if bench12 is not None else None
        future12 = [x[1] for x in p[i + 1:i + 13]]
        mae = min(future12) / entry - 1 if future12 else None
        target = entry + 0.75 * (peak - entry)
        recovery_week = None
        for j, (_, price) in enumerate(p[i + 1:i + 21], start=1):
            if price >= target:
                recovery_week = j
                break
        events.append({
            "entry": datetime.fromtimestamp(p[i][0] / 1000, tz=timezone.utc).date().isoformat(),
            "drawdown": round(dd * 100, 1),
            "return_12w": round(ret12 * 100, 1),
            "excess": round(excess * 100, 1) if excess is not None else None,
            "recovery_weeks": recovery_week,
            "further_downside": round((mae or 0.0) * 100, 1) if mae is not None else None,
        })
        last_entry = i

    n = len(events)
    if n < 4:
        return {
            "ready": True, "label": "Insufficient", "score": None, "samples": n,
            "positive_12w": sum(1 for e in events if e["return_12w"] > 0),
            "recovered_75pct": sum(1 for e in events if e["recovery_weeks"] is not None),
            "median_12w": _median([e["return_12w"] for e in events]),
            "median_excess": _median([e["excess"] for e in events if e["excess"] is not None]),
            "worst": min([e["return_12w"] for e in events], default=None),
            "median_further_downside": _median([e["further_downside"] for e in events if e["further_downside"] is not None]),
            "history": list(reversed(events[-8:])),
            "reason": "Fewer than four non-overlapping historical analogues.",
            "current_drawdown": round(current_dd * 100, 2),
        }

    positive = sum(1 for e in events if e["return_12w"] > 0)
    recovered = sum(1 for e in events if e["recovery_weeks"] is not None)
    med12 = _median([e["return_12w"] for e in events]) or 0.0
    medex = _median([e["excess"] for e in events if e["excess"] is not None]) or 0.0
    worst = min(e["return_12w"] for e in events)
    med_mae = _median([e["further_downside"] for e in events if e["further_downside"] is not None]) or 0.0

    rec_lb = _wilson_lower(recovered, n)
    pos_lb = _wilson_lower(positive, n)
    score = 100 * (
        0.45 * rec_lb
        + 0.25 * pos_lb
        + 0.15 * _clamp((medex + 2.0) / 12.0)
        + 0.10 * _clamp((med12 + 2.0) / 14.0)
        + 0.05 * _clamp(n / 12.0)
    )
    score = round(_clamp(score / 100) * 100)
    label = "Strong" if score >= 65 else ("Moderate" if score >= 50 else "Weak")
    return {
        "ready": True, "label": label, "score": score, "samples": n,
        "positive_12w": positive, "recovered_75pct": recovered,
        "median_12w": round(med12, 1), "median_excess": round(medex, 1),
        "worst": round(worst, 1), "median_further_downside": round(med_mae, 1),
        "history": list(reversed(events[-8:])), "reason": "Calculated from real IBKR daily history; no synthetic episodes.",
        "current_drawdown": round(current_dd * 100, 2),
    }
