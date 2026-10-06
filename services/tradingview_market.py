from __future__ import annotations

import asyncio
import math
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx


def _num(v: Any) -> float | None:
    try:
        if v is None:
            return None
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return None
        return x
    except Exception:
        return None


class TradingViewMoverAPI:
    """Bulk mover discovery using TradingView's public scanner endpoint.

    This is deliberately limited to discovery. Meridian still treats the quoted
    price as reference market data, not an execution feed.
    """

    BASE = "https://scanner.tradingview.com/{market}/scan"

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, dict]] = {}
        self.cache_seconds = 120
        self.timeout = 15.0
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/129 Safari/537.36",
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

    def _payload(self, market: str, limit: int) -> dict:
        us_pre = market == "america" and self.us_session() == "PREMARKET"
        sort_field = "premarket_change" if us_pre else "change"
        return {
            "markets": [market],
            "symbols": {"query": {"types": []}, "tickers": []},
            "options": {"lang": "en"},
            "columns": [
                "name", "description", "close", "change", "volume",
                "relative_volume_10d_calc", "market_cap_basic", "currency",
                "premarket_close", "premarket_change", "premarket_volume",
                "postmarket_close", "postmarket_change", "postmarket_volume",
                "type", "typespecs",
            ],
            "filter": [
                {"left": "is_primary", "operation": "equal", "right": True},
                {"left": "type", "operation": "equal", "right": "stock"},
                {"left": "market_cap_basic", "operation": "greater", "right": 1_000_000_000},
                {"left": "close", "operation": "greater", "right": 5},
            ],
            "sort": {"sortBy": sort_field, "sortOrder": "asc", "nullsFirst": False},
            "range": [0, max(80, min(250, limit * 4))],
            "ignore_unknown_fields": False,
        }

    async def _scan(self, market: str, region_label: str, *, limit: int) -> list[dict]:
        payload = self._payload(market, limit)
        async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers, follow_redirects=True) as client:
            r = await client.post(self.BASE.format(market=market), json=payload)
            r.raise_for_status()
            body = r.json()

        cols = payload["columns"]
        idx = {name: i for i, name in enumerate(cols)}
        us_pre = market == "america" and self.us_session() == "PREMARKET"
        us_after = market == "america" and self.us_session() == "AFTERHOURS"
        rows: list[dict] = []
        for raw in body.get("data") or []:
            symbol_ref = str(raw.get("s") or "")
            vals = raw.get("d") or []
            if not symbol_ref or not vals:
                continue
            def val(name: str):
                i = idx.get(name)
                return vals[i] if i is not None and i < len(vals) else None

            ticker = str(val("name") or symbol_ref.split(":")[-1]).strip()
            name = str(val("description") or ticker).strip()
            close = _num(val("close"))
            reg_change = _num(val("change"))
            pre_close = _num(val("premarket_close"))
            pre_change = _num(val("premarket_change"))
            post_close = _num(val("postmarket_close"))
            post_change = _num(val("postmarket_change"))

            if us_pre and pre_change is not None and pre_close is not None:
                session = "PREMARKET"
                move = pre_change
                # TradingView defines premarket change vs previous regular close.
                reference = pre_close / (1 + pre_change / 100) if abs(1 + pre_change / 100) > 1e-9 else close
                extended = pre_close
                volume = _num(val("premarket_volume"))
            elif us_after and post_change is not None and post_close is not None:
                session = "AFTERHOURS"
                move = post_change
                reference = close
                extended = post_close
                volume = _num(val("postmarket_volume"))
            else:
                session = "REGULAR" if market != "america" or self.us_session() == "REGULAR" else "PREVIOUS_CLOSE"
                move = reg_change
                reference = close
                extended = None
                volume = _num(val("volume"))

            if move is None or move > -1.5:
                continue
            exchange = symbol_ref.split(":", 1)[0] if ":" in symbol_ref else ""
            rows.append({
                "symbol": ticker,
                "name": name,
                "region": region_label,
                "region_code": "us" if market == "america" else "europe",
                "exchange": exchange,
                "exchange_code": exchange,
                "move": round(move, 3),
                "scanner_value": f"{move:.2f}%",
                "price": reference,
                "extended_price": extended,
                "prior_close": reference if session == "PREMARKET" else None,
                "session": session,
                "market_state": session,
                "delay_minutes": None,
                "quote_source": "TradingView Screener",
                "currency": str(val("currency") or ""),
                "market_cap": _num(val("market_cap_basic")),
                "volume": volume,
                "avg_volume": None,
                "relative_volume": _num(val("relative_volume_10d_calc")),
                "tv_symbol": symbol_ref,
            })
        rows.sort(key=lambda x: float(x.get("move") or 999))
        return rows[:limit]

    async def run_mover_scan(self, region: str, *, limit: int = 40, force: bool = False) -> dict:
        region = region.lower().strip()
        if region not in {"all", "us", "europe"}:
            raise ValueError("region must be all, us, or europe")
        now = time.time()
        cached = self._cache.get(region)
        if not force and cached and now - cached[0] < self.cache_seconds:
            return {**cached[1], "cached": True}

        jobs = []
        if region in {"all", "us"}:
            jobs.append(("america", "US"))
        if region in {"all", "europe"}:
            jobs.append(("europe", "Europe"))

        rows: list[dict] = []
        errors: list[str] = []
        results = await asyncio.gather(
            *(self._scan(market, label, limit=limit) for market, label in jobs),
            return_exceptions=True,
        )
        for (market, label), result in zip(jobs, results):
            if isinstance(result, Exception):
                errors.append(f"{label}: {result}")
            else:
                rows.extend(result)

        seen: set[str] = set()
        merged: list[dict] = []
        for item in sorted(rows, key=lambda x: float(x.get("move") or 999)):
            key = str(item.get("tv_symbol") or f"{item.get('region')}:{item.get('symbol')}")
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
            "region": region,
            "rows": merged,
            "errors": errors,
            "updated_at": now,
            "session_note": "TradingView bulk screener. US premarket movers use premarket change versus the previous regular close; Europe uses the latest scanner quote available for each exchange.",
        }
        self._cache[region] = (now, result)
        return {**result, "cached": False}
