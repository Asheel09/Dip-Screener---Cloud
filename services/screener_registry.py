from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "config" / "screeners.json"


def _all() -> list[dict]:
    return json.loads(PATH.read_text(encoding="utf-8"))


def load_screeners(include_disabled: bool = False) -> list[dict]:
    items = _all()
    if not include_disabled:
        items = [x for x in items if x.get("enabled", True)]
    return sorted(items, key=lambda x: x.get("order", 999))


def set_enabled(screener_id: str, enabled: bool) -> list[dict]:
    items = _all()
    found = False
    for item in items:
        if item.get("id") == screener_id:
            item["enabled"] = bool(enabled)
            found = True
            break
    if not found:
        raise ValueError(f"Unknown screener: {screener_id}")
    PATH.write_text(json.dumps(items, indent=2) + "\n", encoding="utf-8")
    return load_screeners(include_disabled=True)
