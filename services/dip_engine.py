from __future__ import annotations
import math


def drawdown(price: float, high: float) -> float:
    return (price / high - 1.0) * 100 if high else 0.0


def severity(row: dict) -> int:
    """Heuristic live severity. Research confidence is kept separate."""
    dd = abs(drawdown(row["price"], row["high13"]))
    day = max(0.0, -float(row.get("day", 0)))
    week = max(0.0, -float(row.get("week", 0)))
    vr = max(0.0, float(row.get("volume_ratio", 1)) - 1.0)
    vol = max(0.12, float(row.get("vol", 0.25)))
    raw = dd * 2.2 + day * 3.0 + week * 1.1 + min(vr, 2.0) * 8.0
    # Higher-vol names need larger moves to reach the same severity.
    normalized = raw / max(0.75, min(1.55, vol / 0.25))
    return int(max(1, min(99, round(normalized))))


def classify(row: dict) -> dict:
    dd = drawdown(row["price"], row["high13"])
    sev = severity(row)
    if sev >= 85:
        label = "Extreme"
    elif sev >= 70:
        label = "High"
    elif sev >= 55:
        label = "Elevated"
    else:
        label = "Normal"
    return {**row, "drawdown": dd, "severity": sev, "severity_label": label}
