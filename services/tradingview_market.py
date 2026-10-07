from __future__ import annotations

import asyncio
import math
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx


US_EXCHANGES = {"NASDAQ", "NYSE", "AMEX", "NYSEARCA", "NYSEAMERICAN"}
MIN_ABS_MOVE = 1.5


def _num(value):
    try:
        x = float(value)
        return None if math.isnan(x) or math.isinf(x) else x
    except Exception:
        return None


class TradingViewMoverAPI:
    """Bulk public TradingView discovery scan used as the cloud fallback.

    v3.4 intentionally scans US-listed stocks only and returns both gainers and
    losers. The personal relay remains preferred because it can also attach
    verified catalyst links without burdening the Render instance.
    """

    BASE = "https://scanner.tradingview.com/{market}/scan"

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, dict]] = {}
        self.cache_seconds = 120
        self.timeout = 15.0
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/129 Safari/537.36",
            "Origin": "https://www.tradingview.com",
            "Referer": "https://www.tradingview.com/",
            "Accept": "application/json,text/plain,*/*",
            "Content-Type": "application/json",
        }

    @staticmethod
    def us_session() -> str:
        now = datetime.now(ZoneInfo("America/New_York"))
        if now.weekday() > 4:
            return "CLOSED"
        mins = now.hour * 60 + now.minute
        if 240 <= mins < 570:
            return "PREMARKET"
        if 570 <= mins < 960:
            return "REGULAR"
        if 960 <= mins < 1200:
            return "AFTERHOURS"
        return "CLOSED"

    def _payload(self, limit: int, sort_order: str) -> dict:
        session = self.us_session()
        sort_field = "premarket_change" if session == "PREMARKET" else "postmarket_change" if session == "AFTERHOURS" else "change"
        return {
            "markets": ["america"],
            "symbols": {"query": {"types": []}, "tickers": []},
            "options": {"lang": "en"},
            "columns": [
                "name", "description", "close", "change", "volume",
                "relative_volume_10d_calc", "market_cap_basic", "currency",
                "premarket_close", "premarket_change", "premarket_volume",
                "postmarket_close", "postmarket_change", "postmarket_volume",
                "type", "typespecs", "market",
            ],
            "filter": [
                {"left": "is_primary", "operation": "equal", "right": True},
                {"left": "type", "operation": "equal", "right": "stock"},
                {"left": "market_cap_basic", "operation": "greater", "right": 1_000_000_000},
                {"left": "close", "operation": "greater", "right": 5},
            ],
            "sort": {"sortBy": sort_field, "sortOrder": sort_order, "nullsFirst": False},
            "range": [0, max(100, min(250, limit * 4))],
            "ignore_unknown_fields": False,
        }

    async def _scan_side(self, *, sort_order: str, limit: int) -> list[dict]:
        payload = self._payload(limit, sort_order)
        async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers, follow_redirects=True) as client:
            r = await client.post(self.BASE.format(market="america"), json=payload)
            r.raise_for_status()
            body = r.json()

        cols = payload["columns"]
        idx = {name: i for i, name in enumerate(cols)}
        session_now = self.us_session()
        rows: list[dict] = []
        for raw in body.get("data") or []:
            symbol_ref = str(raw.get("s") or "")
            vals = raw.get("d") or []
            if not symbol_ref or not vals:
                continue

            def val(name: str):
                i = idx.get(name)
                return vals[i] if i is not None and i < len(vals) else None

            exchange = symbol_ref.split(":", 1)[0].upper() if ":" in symbol_ref else ""
            if exchange not in US_EXCHANGES:
                continue
            ticker = str(val("name") or symbol_ref.split(":")[-1]).strip()
            name = str(val("description") or ticker).strip()
            close = _num(val("close"))
            reg_change = _num(val("change"))
            pre_close = _num(val("premarket_close"))
            pre_change = _num(val("premarket_change"))
            post_close = _num(val("postmarket_close"))
            post_change = _num(val("postmarket_change"))

            if session_now == "PREMARKET" and pre_change is not None and pre_close is not None:
                row_session = "PREMARKET"
                move = pre_change
                reference = pre_close / (1 + pre_change / 100) if abs(1 + pre_change / 100) > 1e-9 else close
                extended = pre_close
                volume = _num(val("premarket_volume"))
            elif session_now == "AFTERHOURS" and post_change is not None and post_close is not None:
                row_session = "AFTERHOURS"
                move = post_change
                reference = close
                extended = post_close
                volume = _num(val("postmarket_volume"))
            else:
                row_session = "REGULAR" if session_now == "REGULAR" else "PREVIOUS_CLOSE"
                move = reg_change
                reference = close
                extended = None
                volume = _num(val("volume"))

            if move is None or abs(move) < MIN_ABS_MOVE:
                continue
            if sort_order == "asc" and move >= 0:
                continue
            if sort_order == "desc" and move <= 0:
                continue

            rows.append({
                "symbol": ticker,
                "name": name,
                "region": "US",
                "region_code": "us",
                "exchange": exchange,
                "exchange_code": exchange,
                "move": round(move, 3),
                "scanner_value": f"{move:.2f}%",
                "price": reference,
                "extended_price": extended,
                "prior_close": reference if row_session == "PREMARKET" else None,
                "session": row_session,
                "market_state": row_session,
                "delay_minutes": None,
                "quote_source": "TradingView Screener",
                "currency": str(val("currency") or ""),
                "market_cap": _num(val("market_cap_basic")),
                "volume": volume,
                "avg_volume": None,
                "relative_volume": _num(val("relative_volume_10d_calc")),
                "tv_symbol": symbol_ref,
                "source_market": str(val("market") or ""),
                "reason": "No verified catalyst found",
                "cause_type": "unverified",
                "reason_verified": False,
            })
        rows.sort(key=lambda x: x["move"], reverse=(sort_order == "desc"))
        return rows[:limit]

    async def run_mover_scan(self, region: str = "us", *, limit: int = 50, force: bool = False) -> dict:
        # "all" is retained as a backward-compatible alias for US-only in v3.4.
        region = region.lower().strip()
        if region not in {"all", "us"}:
            raise ValueError("Mover Radar is US-only in Meridian v3.4")
        region = "us"
        now = time.time()
        cached = self._cache.get(region)
        if not force and cached and now - cached[0] < self.cache_seconds:
            return {**cached[1], "cached": True}

        side_limit = max(10, min(40, (limit + 1) // 2))
        results = await asyncio.gather(
            self._scan_side(sort_order="asc", limit=side_limit),
            self._scan_side(sort_order="desc", limit=side_limit),
            return_exceptions=True,
        )
        rows: list[dict] = []
        errors: list[str] = []
        for label, result in zip(("losers", "gainers"), results):
            if isinstance(result, Exception):
                errors.append(f"US {label}: {result}")
            else:
                rows.extend(result)

        seen: set[str] = set()
        merged: list[dict] = []
        for item in sorted(rows, key=lambda x: abs(float(x.get("move") or 0)), reverse=True):
            key = str(item.get("tv_symbol") or f"US:{item.get('symbol')}")
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
            if len(merged) >= max(1, min(limit, 80)):
                break
        for i, item in enumerate(merged, 1):
            item["rank"] = i

        result = {
            "provider": "tradingview-screener",
            "region": "us",
            "rows": merged,
            "errors": errors,
            "updated_at": now,
            "session_note": "US-listed stocks only. TradingView bulk screening includes both gainers and losers; extended-session moves are labeled explicitly.",
        }
        self._cache[region] = (now, result)
        return {**result, "cached": False}
