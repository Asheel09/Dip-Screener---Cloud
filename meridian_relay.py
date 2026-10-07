#!/usr/bin/env python3
"""Meridian local mover relay — v3.4 compatible.

Runs on one ordinary Mac/Windows laptop, scans liquid US-listed stocks through
TradingView, attaches a *verified* recent catalyst where a relevant free source
can be found, and pushes the mover snapshot to the Render-hosted Meridian site.

Design rules in this build:
- US-listed stocks only (NASDAQ / NYSE / NYSE American families); no Europe.
- Both gainers and losers are collected in one feed.
- Every row receives a reason status. If no trustworthy catalyst is found the
  relay explicitly says "No verified catalyst found" rather than guessing.
- Mover source links reject common paywalled/community/low-signal sources and
  point to the underlying article rather than a generic aggregator page.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

TV_BASE = "https://scanner.tradingview.com/{market}/scan"
GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
NEWS_CACHE_SECONDS = 1800
MOVERS_PER_SIDE = 25
MIN_ABS_MOVE = 1.5
MAX_NEWS_WORKERS = 8
_news_cache: dict[str, tuple[float, dict]] = {}
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/129 Safari/537.36"

# Keep the universe useful for the user's US trading workflow without hard-locking
# it to NASDAQ. NYSE remains important for names such as ORCL, TSM, JPM and LVS.
US_EXCHANGES = {
    "NASDAQ", "NYSE", "AMEX", "NYSEARCA", "NYSEAMERICAN",
}

BLOCKED_DOMAINS = {
    # Explicitly unsuitable for the user's dashboard or commonly paywalled.
    "investing.com", "wsj.com", "bloomberg.com", "ft.com", "barrons.com",
    "marketwatch.com", "seekingalpha.com", "tipranks.com", "thestreet.com",
    "fool.com", "zacks.com", "stocktwits.com", "reddit.com", "quora.com",
    "medium.com",
}

PREFERRED_DOMAINS = {
    "sec.gov": 100,
    "prnewswire.com": 90,
    "businesswire.com": 90,
    "globenewswire.com": 90,
    "reuters.com": 80,
    "cnbc.com": 70,
    "techcrunch.com": 65,
    "apnews.com": 65,
    "nasdaq.com": 60,
}

COLS = [
    "name", "description", "close", "change", "volume",
    "relative_volume_10d_calc", "market_cap_basic", "currency",
    "premarket_close", "premarket_change", "premarket_volume",
    "postmarket_close", "postmarket_change", "postmarket_volume",
    "type", "typespecs", "market",
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
    """Current US Eastern time without requiring tzdata on Windows."""
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


def payload(limit=60, *, sort_order="asc"):
    session = us_session()
    sort_field = (
        "premarket_change" if session == "PREMARKET"
        else "postmarket_change" if session == "AFTERHOURS"
        else "change"
    )
    return {
        "markets": ["america"],
        "symbols": {"query": {"types": []}, "tickers": []},
        "options": {"lang": "en"},
        "columns": COLS,
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


def scan(*, sort_order: str, limit: int = MOVERS_PER_SIDE):
    session = us_session()
    pre = session == "PREMARKET"
    aft = session == "AFTERHOURS"
    pl = payload(limit, sort_order=sort_order)
    body = post_json(TV_BASE.format(market="america"), pl)
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

        exchange = ref.split(":", 1)[0].upper() if ":" in ref else ""
        if exchange not in US_EXCHANGES:
            continue

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
            row_session = "REGULAR" if session == "REGULAR" else "PREVIOUS_CLOSE"
            move = reg
            reference = close
            ext = None
            vol = num(v("volume"))

        if move is None or abs(move) < MIN_ABS_MOVE:
            continue
        if sort_order == "asc" and move >= 0:
            continue
        if sort_order == "desc" and move <= 0:
            continue

        out.append({
            "symbol": ticker,
            "name": name,
            "region": "US",
            "exchange": exchange,
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
            "source_market": str(v("market") or ""),
        })

    out.sort(key=lambda x: x["move"], reverse=(sort_order == "desc"))
    return out[:limit]


def clean_company_name(name: str, symbol: str = "") -> str:
    x = re.sub(
        r"\b(holdings?|incorporated|inc|corporation|corp|company|co|plc|ltd|limited|sa|se|ag|nv|group|class a|class b)\b\.?",
        " ", str(name or symbol), flags=re.I,
    )
    x = re.sub(r"[^A-Za-z0-9&' -]+", " ", x)
    return re.sub(r"\s+", " ", x).strip(" ,-. ") or symbol


def classify_catalyst(headline: str) -> str:
    t = str(headline or "").lower()
    rules = [
        ("earnings", ("earnings", "quarter", "revenue", "profit", "eps", "results", "sales rise", "sales fall")),
        ("guidance", ("guidance", "forecast", "outlook", "raises forecast", "cuts forecast", "profit warning", "expects")),
        ("clinical", ("fda", "clinical", "trial", "phase 1", "phase 2", "phase 3", "drug", "endpoint", "patient", "approval")),
        ("regulatory", ("regulator", "antitrust", "lawsuit", "export control", "sanction", "tariff", "investigation", "fine", "subpoena")),
        ("analyst", ("upgrade", "downgrade", "price target", "rating", "outperform", "underperform", "initiates")),
        ("supply-demand", ("capacity", "production", "supply", "shortage", "demand", "pricing pressure", "competitor", "inventory")),
        ("product", ("launch", "unveil", "release", "product", "chip", "platform", "model", "contract", "order", "partnership")),
        ("corporate", ("acquisition", "merger", "takeover", "deal", "buyback", "dividend", "ceo", "offering", "debt", "stake", "bankruptcy", "layoff", "restructur")),
    ]
    for label, words in rules:
        if any(w in t for w in words):
            return label
    return "unclear"


def _domain(url: str, fallback: str = "") -> str:
    try:
        host = (urlparse(url).hostname or "").lower().removeprefix("www.")
        return host or str(fallback or "").lower().removeprefix("www.")
    except Exception:
        return str(fallback or "").lower().removeprefix("www.")


def _domain_matches(domain: str, target: str) -> bool:
    return domain == target or domain.endswith("." + target)


def source_allowed(url: str, supplied_domain: str = "") -> bool:
    if not re.match(r"^https?://", str(url or ""), flags=re.I):
        return False
    d = _domain(url, supplied_domain)
    if not d:
        return False
    return not any(_domain_matches(d, b) for b in BLOCKED_DOMAINS)


def _headline_has_emoji(headline: str) -> bool:
    return any(unicodedata.category(ch) == "So" for ch in headline)


def headline_relevant(headline: str, company: str, symbol: str) -> bool:
    h = re.sub(r"\s+", " ", str(headline or "")).strip()
    low = h.lower()
    if not h:
        return False
    # Reject community-style / opinion-style snippets such as the ABSI example.
    if _headline_has_emoji(h):
        return False
    if re.search(r"\b(i|i'm|i’ve|i've|my|me|we|we're|we’ve|we've|our)\b", low):
        return False
    if any(x in low for x in ("reddit", "stocktwits", "forum post", "message board", "should you buy", "is it time to buy", "my portfolio")):
        return False

    qname = clean_company_name(company, symbol)
    if qname and qname.lower() in low:
        return True
    if len(symbol) >= 3 and re.search(rf"(?<![A-Z0-9]){re.escape(symbol.upper())}(?![A-Z0-9])", h.upper()):
        return True
    ignore = {"the", "and", "class", "group", "holdings", "technology", "technologies", "systems", "international"}
    tokens = [t.lower() for t in re.findall(r"[A-Za-z0-9]+", qname) if len(t) >= 4 and t.lower() not in ignore]
    return bool(tokens and any(t in low for t in tokens[:3]))


def source_score(url: str, supplied_domain: str = "") -> int:
    d = _domain(url, supplied_domain)
    score = 30
    for target, value in PREFERRED_DOMAINS.items():
        if _domain_matches(d, target):
            score = max(score, value)
    low = str(url or "").lower()
    if any(x in low for x in ("/investor", "/investors", "/newsroom", "/press-release", "/press_releases")):
        score = max(score, 85)
    return score


def get_json(url, params=None, timeout=12):
    if params:
        url = url + ("&" if "?" in url else "?") + urlencode(params)
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/json,*/*"})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _gdelt_reason(symbol: str, company: str) -> dict:
    qname = clean_company_name(company, symbol)
    query = f'"{qname}" sourcelang:english'
    body = get_json(GDELT_URL, {
        "query": query,
        "mode": "ArtList",
        "maxrecords": 30,
        "format": "json",
        "sort": "DateDesc",
        "timespan": "3d",
    })
    candidates = []
    for a in body.get("articles") or []:
        headline = re.sub(r"\s+", " ", str(a.get("title") or "")).strip()
        url = str(a.get("url") or "").strip()
        supplied_domain = str(a.get("domain") or "").strip()
        if not headline_relevant(headline, company, symbol):
            continue
        if not source_allowed(url, supplied_domain):
            continue
        kind = classify_catalyst(headline)
        if kind == "unclear":
            continue
        score = source_score(url, supplied_domain)
        # Prefer newer results when source quality is otherwise comparable.
        seen = str(a.get("seendate") or "")
        candidates.append((score, seen, {
            "reason": headline[:180],
            "cause_type": kind,
            "news_url": url,
            "news_source": _domain(url, supplied_domain) or "source",
            "news_published_at": seen,
            "reason_verified": True,
        }))
    if not candidates:
        return {}
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return candidates[0][2]


def fetch_reason(row: dict) -> dict:
    key = str(row.get("tv_symbol") or f"US:{row.get('symbol')}")
    now = time.time()
    cached = _news_cache.get(key)
    if cached and now - cached[0] < NEWS_CACHE_SECONDS:
        return dict(cached[1])
    try:
        result = _gdelt_reason(str(row.get("symbol") or ""), str(row.get("name") or ""))
    except Exception:
        result = {}
    if not result:
        result = {
            "reason": "No verified catalyst found",
            "cause_type": "unverified",
            "news_url": "",
            "news_source": "",
            "news_published_at": "",
            "reason_verified": False,
        }
    _news_cache[key] = (now, dict(result))
    return result


def enrich_reasons(rows):
    if not rows:
        return rows
    with ThreadPoolExecutor(max_workers=min(MAX_NEWS_WORKERS, len(rows))) as pool:
        jobs = {pool.submit(fetch_reason, row): row for row in rows}
        for job in as_completed(jobs):
            row = jobs[job]
            try:
                row.update(job.result())
            except Exception:
                row.update({
                    "reason": "No verified catalyst found",
                    "cause_type": "unverified",
                    "news_url": "",
                    "news_source": "",
                    "news_published_at": "",
                    "reason_verified": False,
                })
    return rows


def collect():
    errors = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = {
            pool.submit(scan, sort_order="asc", limit=MOVERS_PER_SIDE): "losers",
            pool.submit(scan, sort_order="desc", limit=MOVERS_PER_SIDE): "gainers",
        }
        sides = {"losers": [], "gainers": []}
        for job in as_completed(jobs):
            side = jobs[job]
            try:
                sides[side] = job.result()
            except Exception as e:
                errors.append(f"US {side}: {e}")

    seen = set()
    merged = []
    # Default presentation: largest absolute moves first, regardless of sign.
    for x in sorted(sides["losers"] + sides["gainers"], key=lambda r: abs(float(r["move"])), reverse=True):
        k = x.get("tv_symbol") or f"US:{x['symbol']}"
        if k in seen:
            continue
        seen.add(k)
        merged.append(x)
    enrich_reasons(merged)
    for i, row in enumerate(merged, 1):
        row["rank"] = i
    return merged, errors


def push(base, token, rows):
    url = base.rstrip("/") + "/api/relay/movers"
    return post_json(
        url,
        {"rows": rows, "source": "personal-mac-tradingview", "collected_at": time.time()},
        {
            "X-Meridian-Relay-Token": token,
            "Origin": base.rstrip("/"),
            "Referer": base.rstrip("/") + "/",
        },
    )


def main():
    ap = argparse.ArgumentParser(description="Push local US mover scans to cloud Meridian")
    ap.add_argument("--url", default=os.getenv("MERIDIAN_URL", ""))
    ap.add_argument("--token", default=os.getenv("MERIDIAN_RELAY_TOKEN", ""))
    ap.add_argument("--interval", type=int, default=90)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()

    if not a.url or not a.token:
        print("Need --url and --token (or MERIDIAN_URL / MERIDIAN_RELAY_TOKEN).", file=sys.stderr)
        return 2

    print("Meridian relay v3.4 started. Ctrl+C stops it.")
    print("US movers: TradingView · gainers + losers · verified free-source catalysts via GDELT")

    while True:
        try:
            rows, errors = collect()
            res = push(a.url, a.token, rows)
            verified = sum(1 for r in rows if r.get("reason_verified"))
            print(
                datetime.now().strftime("%H:%M:%S"),
                f"uploaded {len(rows)} movers · {verified} verified catalysts",
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
