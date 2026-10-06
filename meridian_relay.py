#!/usr/bin/env python3
"""Meridian local mover relay (Windows-safe v2).

Runs on one ordinary laptop/network, scans TradingView's bulk US and selected
European equity markets, and securely pushes only the resulting mover rows to
the Render-hosted Meridian instance. No Meridian server or IBKR Gateway is
required locally.

Changes from v1:
- no ZoneInfo/tzdata dependency on Windows
- Europe uses TradingView global/scan with multiple supported market codes
  instead of the invalid /europe/scan endpoint
- only two scanner calls per cycle: US + Europe
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from xml.etree import ElementTree as ET
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

TV_BASE = "https://scanner.tradingview.com/{market}/scan"
GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
NEWS_CACHE_SECONDS = 900
NEWS_ENRICH_LIMIT = 15
_news_cache = {}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/129 Safari/537.36"
EUROPE_MARKETS = ["france", "germany", "uk", "netherlands", "switzerland", "italy"]
COLS = [
    "name", "description", "close", "change", "volume",
    "relative_volume_10d_calc", "market_cap_basic", "currency",
    "premarket_close", "premarket_change", "premarket_volume",
    "postmarket_close", "postmarket_change", "postmarket_volume",
    "type", "typespecs", "market"
]


def num(v):
    try:
        x = float(v)
        return None if math.isnan(x) or math.isinf(x) else x
    except Exception:
        return None


def _nth_sunday(year: int, month: int, nth: int) -> datetime:
    d = datetime(year, month, 1, tzinfo=timezone.utc)
    days_to_sunday = (6 - d.weekday()) % 7
    return d + timedelta(days=days_to_sunday + 7 * (nth - 1))


def _eastern_now() -> datetime:
    """Current US Eastern time without requiring Windows tzdata.

    US DST: second Sunday in March at 02:00 local standard (07:00 UTC)
    through first Sunday in November at 02:00 local daylight (06:00 UTC).
    """
    now_utc = datetime.now(timezone.utc)
    y = now_utc.year
    dst_start_day = _nth_sunday(y, 3, 2)
    dst_end_day = _nth_sunday(y, 11, 1)
    dst_start = dst_start_day.replace(hour=7, minute=0, second=0, microsecond=0)
    dst_end = dst_end_day.replace(hour=6, minute=0, second=0, microsecond=0)
    offset = -4 if dst_start <= now_utc < dst_end else -5
    return now_utc + timedelta(hours=offset)


def us_session() -> str:
    now = _eastern_now()
    if now.weekday() > 4:
        return "CLOSED"
    m = now.hour * 60 + now.minute
    if 240 <= m < 570:
        return "PREMARKET"
    if 570 <= m < 960:
        return "REGULAR"
    if 960 <= m < 1200:
        return "AFTERHOURS"
    return "CLOSED"


def payload(markets, limit=60, premarket_sort=False):
    return {
        "markets": list(markets),
        "symbols": {"query": {"types": []}, "tickers": []},
        "options": {"lang": "en"},
        "columns": COLS,
        "filter": [
            {"left": "is_primary", "operation": "equal", "right": True},
            {"left": "type", "operation": "equal", "right": "stock"},
            {"left": "market_cap_basic", "operation": "greater", "right": 1_000_000_000},
            {"left": "close", "operation": "greater", "right": 5},
        ],
        "sort": {
            "sortBy": "premarket_change" if premarket_sort else "change",
            "sortOrder": "asc",
            "nullsFirst": False,
        },
        "range": [0, max(100, min(250, limit * 4))],
        "ignore_unknown_fields": False,
    }


def post_json(url, obj, headers=None, timeout=20):
    data = json.dumps(obj, separators=(",", ":")).encode()
    h = {
        "User-Agent": UA,
        "Accept": "application/json,text/plain,*/*",
        "Content-Type": "application/json",
        "Origin": "https://www.tradingview.com",
        "Referer": "https://www.tradingview.com/",
    }
    if headers:
        h.update(headers)
    req = Request(url, data=data, headers=h, method="POST")
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def scan(endpoint_market, markets, region, limit=60):
    session = us_session() if region == "US" else "REGULAR"
    pre = region == "US" and session == "PREMARKET"
    aft = region == "US" and session == "AFTERHOURS"

    pl = payload(markets, limit, premarket_sort=pre)
    body = post_json(TV_BASE.format(market=endpoint_market), pl)
    idx = {c: i for i, c in enumerate(COLS)}
    out = []

    for raw in body.get("data") or []:
        ref = str(raw.get("s") or "")
        vals = raw.get("d") or []
        if not ref or not vals:
            continue

        def v(k):
            i = idx[k]
            return vals[i] if i < len(vals) else None

        ticker = str(v("name") or ref.split(":")[-1]).strip()
        name = str(v("description") or ticker).strip()
        close = num(v("close"))
        reg = num(v("change"))
        pc = num(v("premarket_close"))
        pchg = num(v("premarket_change"))
        ac = num(v("postmarket_close"))
        achg = num(v("postmarket_change"))

        if pre and pc is not None and pchg is not None:
            row_session = "PREMARKET"
            move = pchg
            reference = pc / (1 + pchg / 100) if abs(1 + pchg / 100) > 1e-9 else close
            ext = pc
            vol = num(v("premarket_volume"))
        elif aft and ac is not None and achg is not None:
            row_session = "AFTERHOURS"
            move = achg
            reference = close
            ext = ac
            vol = num(v("postmarket_volume"))
        else:
            row_session = "REGULAR" if region != "US" or session == "REGULAR" else "PREVIOUS_CLOSE"
            move = reg
            reference = close
            ext = None
            vol = num(v("volume"))

        if move is None or move > -1.5:
            continue

        ex = ref.split(":", 1)[0] if ":" in ref else ""
        source_market = str(v("market") or "")
        out.append({
            "symbol": ticker,
            "name": name,
            "region": region,
            "exchange": ex,
            "move": round(move, 3),
            "price": reference,
            "extended_price": ext,
            "prior_close": reference if row_session == "PREMARKET" else None,
            "session": row_session,
            "market_state": row_session,
            "delay_minutes": None,
            "quote_source": "Local TradingView relay",
            "currency": str(v("currency") or ""),
            "market_cap": num(v("market_cap_basic")),
            "volume": vol,
            "relative_volume": num(v("relative_volume_10d_calc")),
            "tv_symbol": ref,
            "source_market": source_market,
        })

    return sorted(out, key=lambda x: x["move"])[:limit]


def clean_company_name(name: str, symbol: str = "") -> str:
    x = re.sub(r"\b(holdings?|incorporated|inc|corporation|corp|company|co|plc|ltd|limited|sa|se|ag|nv)\b\.?", " ", str(name or symbol), flags=re.I)
    x = re.sub(r"[^A-Za-z0-9&' -]+", " ", x)
    return re.sub(r"\s+", " ", x).strip(" ,-. ") or symbol


def classify_catalyst(headline: str) -> str:
    t = str(headline or "").lower()
    rules = [
        ("earnings", ("earnings", "quarter", "revenue", "profit", "eps", "results")),
        ("guidance", ("guidance", "forecast", "outlook", "raises forecast", "cuts forecast", "profit warning")),
        ("regulatory", ("regulator", "antitrust", "lawsuit", "export control", "sanction", "tariff", "investigation", "fine")),
        ("analyst", ("upgrade", "downgrade", "price target", "rating", "outperform", "underperform")),
        ("supply-demand", ("capacity", "production", "supply", "shortage", "demand", "pricing pressure", "competitor", "inventory")),
        ("product", ("launch", "unveil", "release", "product", "chip", "platform", "model")),
        ("corporate", ("acquisition", "merger", "takeover", "deal", "buyback", "dividend", "ceo", "offering", "debt", "stake")),
    ]
    for label, words in rules:
        if any(w in t for w in words):
            return label
    return "unclear"


def get_json(url, params=None, timeout=12):
    if params:
        url = url + ("&" if "?" in url else "?") + urlencode(params)
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/json,*/*"})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _gdelt_reason(symbol: str, company: str):
    qname = clean_company_name(company, symbol)
    query = f'"{qname}" sourcelang:english'
    body = get_json(GDELT_URL, {
        "query": query, "mode": "ArtList", "maxrecords": 12,
        "format": "json", "sort": "DateDesc", "timespan": "3d"
    })
    for a in body.get("articles") or []:
        headline = re.sub(r"\s+", " ", str(a.get("title") or "")).strip()
        if not headline:
            continue
        kind = classify_catalyst(headline)
        if kind == "unclear":
            continue
        return {
            "reason": headline[:180],
            "cause_type": kind,
            "news_url": str(a.get("url") or ""),
            "news_source": str(a.get("domain") or "GDELT-indexed source"),
            "news_published_at": str(a.get("seendate") or ""),
        }
    return {}


def _google_reason(symbol: str, company: str):
    qname = clean_company_name(company, symbol)
    params = {"q": f'"{qname}" when:3d', "hl": "en-US", "gl": "US", "ceid": "US:en"}
    url = GOOGLE_NEWS_RSS + "?" + urlencode(params)
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml,application/xml,text/xml,*/*"})
    with urlopen(req, timeout=12) as r:
        root = ET.fromstring(r.read())
    for item in root.findall(".//item")[:12]:
        title = re.sub(r"\s+", " ", (item.findtext("title") or "")).strip()
        if not title:
            continue
        # Google RSS appends publisher with ' - Publisher'; keep the catalyst text concise.
        headline = re.sub(r"\s+-\s+[^-]{2,80}$", "", title).strip() or title
        kind = classify_catalyst(headline)
        if kind == "unclear":
            continue
        return {
            "reason": headline[:180],
            "cause_type": kind,
            "news_url": (item.findtext("link") or "").strip(),
            "news_source": "Google News",
            "news_published_at": (item.findtext("pubDate") or "").strip(),
        }
    return {}


def fetch_reason(row: dict) -> dict:
    key = str(row.get("tv_symbol") or f"{row.get('region')}:{row.get('symbol')}")
    now = time.time()
    cached = _news_cache.get(key)
    if cached:
        ttl = NEWS_CACHE_SECONDS if cached[1] else 300
        if now - cached[0] < ttl:
            return dict(cached[1])
    result = {}
    try:
        result = _gdelt_reason(str(row.get("symbol") or ""), str(row.get("name") or ""))
    except Exception:
        result = {}
    if not result:
        try:
            result = _google_reason(str(row.get("symbol") or ""), str(row.get("name") or ""))
        except Exception:
            result = {}
    _news_cache[key] = (now, dict(result))
    return result


def enrich_reasons(rows):
    # News lookup is only for the largest movers; smaller rows remain blank rather than speculative.
    targets = [r for r in rows if num(r.get("move")) is not None and num(r.get("move")) <= -3][:NEWS_ENRICH_LIMIT]
    if not targets:
        return rows
    with ThreadPoolExecutor(max_workers=min(5, len(targets))) as pool:
        jobs = {pool.submit(fetch_reason, row): row for row in targets}
        for job in as_completed(jobs):
            row = jobs[job]
            try:
                row.update(job.result())
            except Exception:
                pass
    return rows


def collect():
    rows = []
    errors = []
    jobs = [
        ("america", ["america"], "US"),
        ("global", EUROPE_MARKETS, "Europe"),
    ]
    for endpoint_market, markets, region in jobs:
        try:
            rows.extend(scan(endpoint_market, markets, region))
        except Exception as e:
            errors.append(f"{region}: {e}")

    seen = set()
    merged = []
    for x in sorted(rows, key=lambda x: x["move"]):
        k = x.get("tv_symbol") or f"{x['region']}:{x['symbol']}"
        if k in seen:
            continue
        seen.add(k)
        merged.append(x)
        if len(merged) >= 100:
            break
    enrich_reasons(merged)
    return merged, errors


def push(base, token, rows):
    url = base.rstrip("/") + "/api/relay/movers"
    return post_json(
        url,
        {"rows": rows, "source": "work-laptop-tradingview", "collected_at": time.time()},
        {
            "X-Meridian-Relay-Token": token,
            "Origin": base.rstrip("/"),
            "Referer": base.rstrip("/") + "/",
        },
    )


def main():
    ap = argparse.ArgumentParser(description="Push local mover scans to cloud Meridian")
    ap.add_argument("--url", default=os.getenv("MERIDIAN_URL", ""))
    ap.add_argument("--token", default=os.getenv("MERIDIAN_RELAY_TOKEN", ""))
    ap.add_argument("--interval", type=int, default=90)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()

    if not a.url or not a.token:
        print("Need --url and --token (or MERIDIAN_URL / MERIDIAN_RELAY_TOKEN).", file=sys.stderr)
        return 2

    print("Meridian relay v3 started. Ctrl+C stops it.")
    print("US source: TradingView america | Europe source: TradingView global (FR/DE/UK/NL/CH/IT) | Reasons: local GDELT/Google News")

    while True:
        try:
            rows, errors = collect()
            res = push(a.url, a.token, rows)
            print(
                datetime.now().strftime("%H:%M:%S"),
                f"uploaded {len(rows)} movers",
                (" | " + "; ".join(errors) if errors else ""),
                res,
            )
        except (HTTPError, URLError, TimeoutError, Exception) as e:
            print(datetime.now().strftime("%H:%M:%S"), "relay error:", e, file=sys.stderr)

        if a.once:
            return 0
        time.sleep(max(45, a.interval))


if __name__ == "__main__":
    raise SystemExit(main())
