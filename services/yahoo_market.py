from __future__ import annotations

import asyncio
import math
import statistics
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any


EUROPE_REGIONS = ["fr", "de", "gb", "nl", "ch", "it"]
REGION_LABELS = {
    "us": "US", "fr": "France", "de": "Germany", "gb": "UK",
    "nl": "Netherlands", "ch": "Switzerland", "it": "Italy",
}


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


def _yahoo_symbol(symbol: str) -> str:
    # Yahoo uses hyphens for US share classes.
    return {"BRK.B": "BRK-B", "BF.B": "BF-B"}.get(symbol.upper(), symbol.upper())


class YahooMarketAPI:
    """Small async wrapper around yfinance for Meridian's cloud mode.

    Yahoo is used as a discovery/reference-data source, not an execution feed.
    The screener response exposes the exchange's delay in `exchangeDataDelayedBy`.
    """

    def __init__(self) -> None:
        try:
            import yfinance as yf  # type: ignore
        except Exception as exc:  # pragma: no cover - only happens on broken deploys
            raise RuntimeError("Cloud market data requires the yfinance package") from exc
        self.yf = yf
        self._scan_cache: dict[str, tuple[float, dict]] = {}
        self._history_cache: dict[str, tuple[float, list[dict]]] = {}
        self.scan_cache_seconds = 120
        self.history_cache_seconds = 7 * 24 * 3600

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

    @staticmethod
    def _screen_move(q: dict) -> tuple[str, float | None, float | None, float | None]:
        state = str(q.get("marketState") or "CLOSED").upper()
        prev = _num(q.get("regularMarketPreviousClose"))
        regular = _num(q.get("regularMarketPrice"))
        pre = _num(q.get("preMarketPrice"))
        post = _num(q.get("postMarketPrice"))
        pre_pct = _num(q.get("preMarketChangePercent"))
        post_pct = _num(q.get("postMarketChangePercent"))
        reg_pct = _num(q.get("regularMarketChangePercent"))

        if state in {"PRE", "PREPRE"} and pre is not None:
            pct = pre_pct if pre_pct is not None else ((pre / prev - 1) * 100 if prev else None)
            return "PREMARKET", pct, prev or regular, pre
        if state == "POST" and post is not None:
            pct = post_pct if post_pct is not None else ((post / regular - 1) * 100 if regular else None)
            return "AFTERHOURS", pct, regular, post
        if state == "REGULAR":
            return "REGULAR", reg_pct, regular, None
        return "PREVIOUS_CLOSE", reg_pct, regular, None

    def _screen_region_sync(self, region: str, *, limit: int = 50) -> list[dict]:
        yf = self.yf
        EquityQuery = yf.EquityQuery
        filters = [
            EquityQuery("eq", ["region", region]),
            EquityQuery("gte", ["intradaymarketcap", 1_000_000_000]),
            EquityQuery("gte", ["intradayprice", 5]),
        ]
        q = EquityQuery("and", filters)

        # Before the US open, a screen sorted by normal-session percent change would
        # rank yesterday's losers. Pull a broad large-cap sample instead, then rank
        # locally by premarket change when Yahoo supplies it.
        premarket_us = region == "us" and self.us_session() == "PREMARKET"
        size = 250 if premarket_us else max(60, min(250, limit * 3))
        if premarket_us:
            quotes = []
            # Cover roughly the largest 500 US companies, then rank locally by
            # premarket % move. This is much more useful before 09:30 ET than
            # Yahoo's normal-session day-loser ranking.
            for offset in (0, 250):
                response = yf.screen(q, offset=offset, size=250, sortField="intradaymarketcap", sortAsc=False) or {}
                quotes.extend(response.get("quotes") or [])
        else:
            response = yf.screen(q, size=size, sortField="percentchange", sortAsc=True) or {}
            quotes = response.get("quotes") or []
        rows: list[dict] = []
        blocked = (" ETF", " FUND", " PROSHARES", " ISHARES", " SPDR", " DIREXION", " ULTRAPRO", " 2X ", " 3X ")
        for raw in quotes:
            symbol = str(raw.get("symbol") or "").strip()
            name = str(raw.get("longName") or raw.get("shortName") or raw.get("displayName") or symbol).strip()
            if not symbol or any(x in f" {name.upper()} " for x in blocked):
                continue
            session, move, reference_price, extended_price = self._screen_move(raw)
            if move is None:
                continue
            # Mover Radar is for meaningful downside dislocations, not every red name.
            if move > -1.5:
                continue
            rows.append({
                "symbol": symbol,
                "name": name,
                "region": REGION_LABELS.get(region, region.upper()),
                "region_code": region,
                "exchange": str(raw.get("fullExchangeName") or raw.get("exchange") or ""),
                "exchange_code": str(raw.get("exchange") or ""),
                "move": round(move, 3),
                "scanner_value": f"{move:.2f}%",
                "price": reference_price,
                "extended_price": extended_price,
                "prior_close": _num(raw.get("regularMarketPreviousClose")),
                "session": session,
                "market_state": str(raw.get("marketState") or ""),
                "delay_minutes": int(_num(raw.get("exchangeDataDelayedBy")) or 0),
                "quote_source": str(raw.get("quoteSourceName") or "Yahoo Finance"),
                "currency": str(raw.get("currency") or ""),
                "market_cap": _num(raw.get("marketCap")),
                "volume": _num(raw.get("regularMarketVolume")),
                "avg_volume": _num(raw.get("averageDailyVolume3Month")),
            })
        rows.sort(key=lambda x: float(x.get("move") or 999))
        return rows[:limit]

    async def run_mover_scan(self, region: str, *, limit: int = 40, force: bool = False) -> dict:
        region = region.lower().strip()
        if region not in {"all", "us", "europe"}:
            raise ValueError("region must be all, us, or europe")
        now = time.time()
        cached = self._scan_cache.get(region)
        if not force and cached and now - cached[0] < self.scan_cache_seconds:
            return {**cached[1], "cached": True}

        region_codes = ["us"] if region == "us" else (EUROPE_REGIONS if region == "europe" else ["us", *EUROPE_REGIONS])
        all_rows: list[dict] = []
        errors: list[str] = []
        # Sequential calls are gentler on Yahoo and plenty fast at a 2-minute cache cadence.
        for code in region_codes:
            try:
                all_rows.extend(await asyncio.to_thread(self._screen_region_sync, code, limit=limit))
            except Exception as exc:
                errors.append(f"{REGION_LABELS.get(code, code)}: {exc}")
            await asyncio.sleep(0.05)

        # de-duplicate ADR / same-symbol duplicates conservatively by region+symbol
        seen: set[str] = set()
        rows: list[dict] = []
        for item in sorted(all_rows, key=lambda x: float(x.get("move") or 999)):
            key = f"{item.get('region_code')}:{item.get('symbol')}"
            if key in seen:
                continue
            seen.add(key)
            rows.append(item)
            if len(rows) >= max(1, min(limit, 80)):
                break
        for i, item in enumerate(rows, 1):
            item["rank"] = i
        result = {
            "provider": "yahoo-screener",
            "region": region,
            "rows": rows,
            "errors": errors,
            "updated_at": now,
            "session_note": "US premarket uses premarket change when Yahoo supplies it; otherwise closed US names show the previous regular-session move. European rows use the latest exchange quote available to Yahoo.",
        }
        self._scan_cache[region] = (now, result)
        return {**result, "cached": False}

    def _history_sync(self, symbol: str, period: str = "5y") -> list[dict]:
        ticker = self.yf.Ticker(_yahoo_symbol(symbol))
        df = ticker.history(period=period, interval="1d", auto_adjust=False, actions=False, prepost=False)
        out: list[dict] = []
        if df is None or getattr(df, "empty", True):
            return out
        for ts, row in df.iterrows():
            c = _num(row.get("Close")); h = _num(row.get("High")); l = _num(row.get("Low")); o = _num(row.get("Open")); v = _num(row.get("Volume"))
            if c is None or c <= 0:
                continue
            try:
                epoch_ms = int(ts.timestamp() * 1000)
            except Exception:
                continue
            out.append({"t": epoch_ms, "o": o, "h": h, "l": l, "c": c, "v": v or 0})
        return out

    async def history_bars(self, symbol: str, period: str = "5y") -> list[dict]:
        key = f"{symbol.upper()}:{period}"
        now = time.time()
        cached = self._history_cache.get(key)
        if cached and now - cached[0] < self.history_cache_seconds:
            return cached[1]
        bars = await asyncio.to_thread(self._history_sync, symbol, period)
        if bars:
            self._history_cache[key] = (now, bars)
        return bars

    async def history_features(self, symbol: str) -> dict[str, Any]:
        bars = await self.history_bars(symbol, "5y")
        if len(bars) < 6:
            raise RuntimeError(f"Not enough Yahoo history returned for {symbol}")
        highs = [float(b["h"] or b["c"]) for b in bars]
        closes = [float(b["c"]) for b in bars]
        vols = [float(b.get("v") or 0) for b in bars if float(b.get("v") or 0) > 0]
        rets = [closes[i] / closes[i - 1] - 1 for i in range(max(1, len(closes) - 61), len(closes)) if closes[i - 1] > 0]
        ann_vol = statistics.pstdev(rets) * math.sqrt(252) if len(rets) >= 5 else .25
        return {
            "high13": max(highs[-66:]),
            "week_ref": closes[-6],
            "avg_volume": statistics.mean(vols[-60:]) if vols else 0.0,
            "vol": max(.08, min(1.5, ann_vol)),
            "history_last": closes[-1],
            "bars": bars,
        }

    def _core_sync(self, symbols: list[str]) -> dict[str, dict]:
        """Refresh a compact core list from daily history.

        Daily data is deliberate: before the US open the primary displayed price is
        the previous regular close. Mover Radar separately exposes premarket moves.
        """
        if not symbols:
            return {}
        ys = [_yahoo_symbol(s) for s in symbols]
        df = self.yf.download(ys, period="3mo", interval="1d", group_by="ticker", auto_adjust=False, progress=False, threads=True)
        out: dict[str, dict] = {}
        if df is None or getattr(df, "empty", True):
            return out
        multi = getattr(df.columns, "nlevels", 1) > 1
        for original, ysmb in zip(symbols, ys):
            try:
                frame = df[ysmb] if multi else df
                frame = frame.dropna(subset=["Close"])
                if len(frame) < 6:
                    continue
                closes = [float(x) for x in frame["Close"].tolist() if _num(x) is not None]
                highs = [float(x) for x in frame["High"].tolist() if _num(x) is not None]
                vols = [float(x) for x in frame["Volume"].tolist() if _num(x) is not None and float(x) > 0]
                if len(closes) < 6:
                    continue
                last = closes[-1]
                prev = closes[-2]
                week_ref = closes[-6]
                rets = [closes[i] / closes[i-1] - 1 for i in range(max(1,len(closes)-61), len(closes)) if closes[i-1] > 0]
                ann_vol = statistics.pstdev(rets) * math.sqrt(252) if len(rets) >= 5 else .25
                out[original] = {
                    "price": last,
                    "prior_close": prev,
                    "day": (last / prev - 1) * 100 if prev else 0.0,
                    "week": (last / week_ref - 1) * 100 if week_ref else 0.0,
                    "high13": max(highs[-66:]) if highs else last,
                    "vol": max(.08, min(1.5, ann_vol)),
                    "avg_volume": statistics.mean(vols[-20:]) if vols else 0.0,
                    "volume": vols[-1] if vols else 0.0,
                }
            except Exception:
                continue
        return out

    async def core_quotes(self, symbols: list[str]) -> dict[str, dict]:
        return await asyncio.to_thread(self._core_sync, symbols)
