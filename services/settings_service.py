from __future__ import annotations
import json
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "config" / "runtime_settings.json"

DEFAULTS = {
    "push_ms": 1000,
    "max_live_rows": 250,
    "min_drawdown": 5.0,
    "max_drawdown": 60.0,
    "min_severity": 1,
    "compact_mode": False,
    "show_news_preview": True,
    "ai_web_search": False,
}

BOUNDS = {
    "push_ms": (250, 10000),
    "max_live_rows": (10, 1000),
    "min_drawdown": (0.0, 50.0),
    "max_drawdown": (5.0, 95.0),
    "min_severity": (1, 99),
}

class SettingsService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        PATH.parent.mkdir(parents=True, exist_ok=True)
        if not PATH.exists():
            self._write(DEFAULTS.copy())

    def _read(self) -> dict:
        try:
            data = json.loads(PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        return {**DEFAULTS, **data}

    def _write(self, data: dict) -> None:
        tmp = PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        tmp.replace(PATH)

    def get(self) -> dict:
        with self._lock:
            return self._read()

    def update(self, changes: dict) -> dict:
        allowed = set(DEFAULTS)
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Unsupported setting(s): {', '.join(sorted(unknown))}")
        with self._lock:
            data = self._read()
            for key, value in changes.items():
                if key in BOUNDS:
                    lo, hi = BOUNDS[key]
                    value = max(lo, min(hi, float(value)))
                    if key in {"push_ms", "max_live_rows", "min_severity"}:
                        value = int(round(value))
                elif key in {"compact_mode", "show_news_preview", "ai_web_search"}:
                    value = bool(value)
                data[key] = value
            if float(data["max_drawdown"]) < float(data["min_drawdown"]):
                raise ValueError("Maximum drawdown must be at least the minimum drawdown.")
            self._write(data)
            return data

    def reset(self) -> dict:
        with self._lock:
            data = DEFAULTS.copy()
            self._write(data)
            return data
