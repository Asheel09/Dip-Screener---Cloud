"""Primary comparable-dislocation model.

The model intentionally does *not* use calendar seasonality as the entry signal.
It asks whether the stock is in a fresh, statistically unusual drawdown and how
similar historical drawdowns behaved after a small turning confirmation.
"""
from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass

import pandas as pd

from screener import benchmark_return

LOOKBACK_HIGH_WEEKS = 16
RECENT_LOW_WEEKS = 8
MAX_RECOVERY_WEEKS = 20
PRIMARY_MIN_DRAWDOWN = 0.10
PRIMARY_MAX_DRAWDOWN = 0.25
DEEP_DRAWDOWN = 0.25
RARITY_MAX = 0.25
MIN_SAMPLES = 6
MIN_RECOVERY_RATE = 0.60
MIN_POSITIVE_12W = 0.60
MIN_MEDIAN_12W = 0.03
MIN_MEDIAN_EXCESS_12W = 0.00
MIN_Q25_12W = -0.10
RECOVERY_FRACTION = 0.75


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    xs = sorted(float(x) for x in values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return xs[lo]
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def _wilson_lower(successes: int, total: int, z: float = 1.645) -> float:
    """Approximate 90% Wilson lower bound; penalises small samples."""
    if total <= 0:
        return 0.0
    phat = successes / total
    denom = 1 + z * z / total
    centre = phat + z * z / (2 * total)
    adj = z * math.sqrt((phat * (1 - phat) + z * z / (4 * total)) / total)
    return max(0.0, (centre - adj) / denom)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _index_of_max(s: pd.Series) -> int:
    return int(s.values.argmax())


def _index_of_min(s: pd.Series) -> int:
    return int(s.values.argmin())


def _ret_at(p: pd.Series, i: int, weeks: int) -> float | None:
    j = i + weeks
    if j >= len(p):
        return None
    return float(p.iloc[j] / p.iloc[i] - 1)


def _mae(p: pd.Series, i: int, weeks: int) -> float | None:
    future = p.iloc[i + 1:min(len(p), i + 1 + weeks)]
    if future.empty:
        return None
    return float(future.min() / p.iloc[i] - 1)


def _drawdown_at(p: pd.Series, i: int, lookback: int = LOOKBACK_HIGH_WEEKS) -> tuple[float, float, int]:
    start = max(0, i - lookback + 1)
    window = p.iloc[start:i + 1]
    rel = _index_of_max(window)
    peak_i = start + rel
    peak = float(p.iloc[peak_i])
    current = float(p.iloc[i])
    return (float(current / peak - 1) if peak > 0 else 0.0, peak, peak_i)


def _rarity_percentile(p: pd.Series, current_dd: float) -> float:
    dds: list[float] = []
    # Leave the most recent six months out of the historical reference distribution.
    stop = max(LOOKBACK_HIGH_WEEKS, len(p) - 26)
    for i in range(LOOKBACK_HIGH_WEEKS, stop):
        dd, _, _ = _drawdown_at(p, i)
        dds.append(dd)
    if not dds:
        return 1.0
    return sum(x <= current_dd for x in dds) / len(dds)


def _current_shape(p: pd.Series) -> dict:
    i = len(p) - 1
    dd, peak, peak_i = _drawdown_at(p, i)
    recent = p.iloc[max(0, i - RECENT_LOW_WEEKS + 1):i + 1]
    low_rel = _index_of_min(recent)
    low_i = max(0, i - RECENT_LOW_WEEKS + 1) + low_rel
    low = float(p.iloc[low_i])
    bounce = float(p.iloc[i] / low - 1) if low > 0 else 0.0
    weeks_since_low = i - low_i
    last2 = float(p.iloc[-1] / p.iloc[-3] - 1) if len(p) >= 3 else 0.0
    last4 = float(p.iloc[-1] / p.iloc[-5] - 1) if len(p) >= 5 else 0.0
    recent6 = p.iloc[-6:]
    six_range = float(recent6.max() / recent6.min() - 1) if len(recent6) >= 2 else 0.0
    six_change = float(recent6.iloc[-1] / recent6.iloc[0] - 1) if len(recent6) >= 2 else 0.0
    stale_plateau = weeks_since_low >= 4 and six_range <= .06 and abs(six_change) <= .03
    turning = bounce >= .02 or last2 >= .01 or last4 > 0
    ret26 = float(p.iloc[-1] / p.iloc[-27] - 1) if len(p) >= 27 else 0.0
    med40 = float(p.iloc[-40:].median()) if len(p) >= 40 else float(p.median())
    gap40 = float(p.iloc[-1] / med40 - 1) if med40 > 0 else 0.0
    # Structural-risk flag is intentionally conservative. It does not prove fundamentals changed;
    # it says the price path no longer looks like an ordinary correction.
    regime_risk = (ret26 <= -.30) or (gap40 <= -.25 and bounce < .05)
    return {
        'current_drawdown': dd, 'current_peak_price': peak,
        'current_peak_date': p.index[peak_i].date().isoformat(),
        'bounce_from_recent_low': bounce, 'weeks_since_recent_low': weeks_since_low,
        'last2_change': last2, 'last4_change': last4,
        'stale_plateau': stale_plateau, 'turning_confirmation': turning,
        'return_26w': ret26, 'gap_to_40w_median': gap40,
        'regime_risk': regime_risk,
    }


def _historical_events(p: pd.Series, b: pd.Series, current_dd: float) -> list[dict]:
    tol = max(.035, abs(current_dd) * .30)
    lo, hi = current_dd - tol, current_dd + tol
    events: list[dict] = []
    i = 30
    last_entry = -99
    while i + MAX_RECOVERY_WEEKS < len(p) - 2:
        dd, peak, peak_i = _drawdown_at(p, i)
        if not (lo <= dd <= hi):
            i += 1
            continue
        # Require a real prior peak and a small turn from a recent low, to mimic the live entry shape.
        if peak_i >= i - 1:
            i += 1
            continue
        recent_start = max(0, i - RECENT_LOW_WEEKS + 1)
        recent = p.iloc[recent_start:i + 1]
        low_rel = _index_of_min(recent)
        low_i = recent_start + low_rel
        low = float(p.iloc[low_i])
        bounce = float(p.iloc[i] / low - 1) if low > 0 else 0.0
        last2 = float(p.iloc[i] / p.iloc[i - 2] - 1) if i >= 2 else 0.0
        if not (bounce >= .015 or last2 >= .005):
            i += 1
            continue
        # Avoid matching entries that were already in a long decline before the local peak.
        if peak_i >= 13:
            pre_peak_trend = float(p.iloc[peak_i] / p.iloc[peak_i - 13] - 1)
            if pre_peak_trend <= -.10:
                i += 1
                continue
        if i - last_entry < 10:
            i += 1
            continue

        entry = float(p.iloc[i])
        target = entry + RECOVERY_FRACTION * (peak - entry)
        future = p.iloc[i + 1:i + 1 + MAX_RECOVERY_WEEKS]
        reached = [j for j, v in enumerate(future, start=1) if float(v) >= target]
        recovery_week = reached[0] if reached else None
        ret12 = _ret_at(p, i, 12)
        ret20 = _ret_at(p, i, 20)
        bench12 = benchmark_return(b, p.index[i], p.index[i + 12]) if i + 12 < len(p) else None
        excess12 = (ret12 - bench12) if (ret12 is not None and bench12 is not None) else None
        events.append({
            'entry_date': p.index[i].date().isoformat(),
            'peak_date': p.index[peak_i].date().isoformat(),
            'entry_price': entry, 'peak_price': peak, 'drawdown': dd,
            'bounce_at_entry': bounce,
            'recovery_end': p.index[i + recovery_week].date().isoformat() if recovery_week is not None else '',
            'recovery_weeks': recovery_week,
            'return_12w': ret12, 'return_20w': ret20, 'excess_12w': excess12,
            'mae_12w': _mae(p, i, 12), 'mae_20w': _mae(p, i, 20),
            'target_price': target,
        })
        last_entry = i
        i += 8
    return events


def comparable_dislocation_candidate(p: pd.Series, b: pd.Series) -> tuple[str, dict, str]:
    """Return PASS / WATCH / FAIL plus transparent comparable-drop statistics."""
    if len(p) < 180:
        return 'FAIL', {}, 'Insufficient history for comparable-dislocation model'
    cur = _current_shape(p)
    dd = cur['current_drawdown']
    rarity = _rarity_percentile(p, dd)
    cur['drawdown_rarity_percentile'] = rarity
    deep = dd <= -DEEP_DRAWDOWN
    in_primary_band = -PRIMARY_MAX_DRAWDOWN <= dd <= -PRIMARY_MIN_DRAWDOWN
    fresh = not cur['stale_plateau'] and cur['turning_confirmation']
    unusual = rarity <= RARITY_MAX
    if not (fresh and unusual and (in_primary_band or deep)):
        state = []
        if dd > -PRIMARY_MIN_DRAWDOWN:
            state.append('drawdown too small')
        if cur['stale_plateau']:
            state.append('stale lower plateau')
        if not cur['turning_confirmation']:
            state.append('no turning confirmation')
        if not unusual:
            state.append('drawdown not unusual for this stock')
        return 'FAIL', {**cur}, '; '.join(state) or 'No qualifying dislocation'

    events = _historical_events(p, b, dd)
    n = len(events)
    recovered20 = [e for e in events if e['recovery_weeks'] is not None]
    recovered12 = [e for e in events if e['recovery_weeks'] is not None and e['recovery_weeks'] <= 12]
    ret12 = [e['return_12w'] for e in events if e['return_12w'] is not None]
    ret20 = [e['return_20w'] for e in events if e['return_20w'] is not None]
    excess = [e['excess_12w'] for e in events if e['excess_12w'] is not None]
    mae12 = [e['mae_12w'] for e in events if e['mae_12w'] is not None]
    pos12 = sum(x > 0 for x in ret12)
    rec20_rate = len(recovered20) / n if n else 0.0
    rec12_rate = len(recovered12) / n if n else 0.0
    pos12_rate = pos12 / len(ret12) if ret12 else 0.0
    med12 = float(statistics.median(ret12)) if ret12 else None
    med20 = float(statistics.median(ret20)) if ret20 else None
    medex = float(statistics.median(excess)) if excess else None
    q25 = _percentile(ret12, .25) if ret12 else None
    worst12 = min(ret12) if ret12 else None
    med_mae = float(statistics.median(mae12)) if mae12 else None
    worst_mae = min(mae12) if mae12 else None
    med_rec = float(statistics.median(e['recovery_weeks'] for e in recovered20)) if recovered20 else None
    rec_lb = _wilson_lower(len(recovered20), n)
    pos_lb = _wilson_lower(pos12, len(ret12)) if ret12 else 0.0

    # Reliability is deliberately dominated by repeatability and tail behaviour,
    # not by the single highest historical return.
    reliability = 100 * (
        .40 * rec_lb +
        .20 * pos_lb +
        .15 * _clamp((medex or 0.0) / .08) +
        .10 * _clamp((med12 or 0.0) / .12) +
        .10 * _clamp(((q25 if q25 is not None else -.15) + .12) / .12) +
        .05 * _clamp(n / 12)
    )
    if cur['regime_risk']:
        reliability -= 12
    if deep:
        reliability -= 10
    reliability = round(_clamp(reliability / 100) * 100, 1)

    minimum_pass = (
        n >= MIN_SAMPLES and rec20_rate >= MIN_RECOVERY_RATE and
        pos12_rate >= MIN_POSITIVE_12W and med12 is not None and med12 >= MIN_MEDIAN_12W and
        medex is not None and medex > MIN_MEDIAN_EXCESS_12W and q25 is not None and q25 >= MIN_Q25_12W and
        not cur['regime_risk'] and not deep
    )
    grade = 'C'
    status = 'WATCH'
    if minimum_pass:
        grade = 'B'
        status = 'PASS'
        if (n >= 8 and rec20_rate >= .70 and pos12_rate >= .65 and
                med12 >= .05 and medex >= .02 and q25 >= -.06 and rec_lb >= .45 and reliability >= 60):
            grade = 'A'
    elif n < 5 or med12 is None or medex is None or med12 <= 0 or medex <= -.02:
        status = 'FAIL'

    stats = {
        **cur,
        'strategy': 'Comparable dislocation recovery',
        'signal_grade': grade,
        'reliability_score': reliability,
        'sample_size': n,
        'recovery_rate': rec20_rate,
        'recovery_12w_rate': rec12_rate,
        'median_weeks_to_recover': med_rec,
        'positive_12w_rate': pos12_rate,
        'median_forward_return': med12,
        'median_20w_return': med20,
        'median_excess_return': medex,
        'q25_12w_return': q25,
        'worst_forward_return': worst12,
        'median_mae_12w': med_mae,
        'worst_mae_12w': worst_mae,
        'recovery_target_fraction': RECOVERY_FRACTION,
        'comparable_history_json': json.dumps(events, separators=(',', ':')),
    }
    detail = (
        f"Current drawdown {dd:.1%} from the prior {LOOKBACK_HIGH_WEEKS}-week high; "
        f"only {rarity:.0%} of historical weekly observations were this weak or weaker. "
        f"Bounce from the recent low {cur['bounce_from_recent_low']:.1%}. "
        f"Found {n} non-overlapping comparable historical entries: {rec20_rate:.0%} recovered "
        f"{RECOVERY_FRACTION:.0%} of the drawdown within {MAX_RECOVERY_WEEKS} weeks; "
        f"{pos12_rate:.0%} had positive 12-week returns; median 12-week return "
        f"{('—' if med12 is None else f'{med12:+.1%}')}, median excess vs SPY "
        f"{('—' if medex is None else f'{medex:+.1%}')}."
    )
    return status, stats, detail
