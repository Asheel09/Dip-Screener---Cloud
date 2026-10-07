from __future__ import annotations

import asyncio
import hashlib
import html
import os
import re
import unicodedata
from urllib.parse import urlparse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from xml.etree import ElementTree as ET

import httpx

from .event_store import EventStore

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

OFFICIAL_FEEDS = [
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("BLS", "https://www.bls.gov/feed/bls_latest.rss"),
]

MATERIAL_FORMS = {"8-K", "8-K/A", "10-Q", "10-Q/A", "10-K", "10-K/A", "6-K", "20-F", "40-F"}

BLOCKED_NEWS_DOMAINS = {
    "investing.com", "wsj.com", "bloomberg.com", "ft.com", "barrons.com",
    "marketwatch.com", "seekingalpha.com", "tipranks.com", "thestreet.com",
    "fool.com", "zacks.com", "stocktwits.com", "reddit.com", "quora.com",
    "medium.com",
}
PREFERRED_NEWS_DOMAINS = {
    "sec.gov": 100, "prnewswire.com": 90, "businesswire.com": 90,
    "globenewswire.com": 90, "reuters.com": 80, "cnbc.com": 70,
    "techcrunch.com": 65, "apnews.com": 65, "nasdaq.com": 60,
}


def _clean(text: Any) -> str:
    raw = html.unescape(str(text or ""))
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)).strip()


def _iso_date(value: Any) -> str | None:
    if not value:
        return None
    s = str(value).strip()
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return s


def _event_key(source: str, url: str, headline: str) -> str:
    base = url.strip() or f"{source}|{headline.strip().lower()}"
    return hashlib.sha256(base.encode("utf-8", errors="ignore")).hexdigest()


def _classify(headline: str, summary: str = "") -> str:
    t = f"{headline} {summary}".lower()
    rules = [
        ("earnings", ("earnings", "quarter", "revenue", "profit", "eps", "results")),
        ("guidance", ("guidance", "forecast", "outlook", "expects", "raises forecast", "cuts forecast", "profit warning")),
        ("clinical", ("fda", "clinical", "trial", "phase 1", "phase 2", "phase 3", "drug", "endpoint", "approval")),
        ("regulatory", ("sec filing", "regulator", "antitrust", "lawsuit", "export control", "sanction", "tariff", "investigation", "fine", "subpoena")),
        ("analyst", ("upgrade", "downgrade", "price target", "rating", "initiates", "outperform", "underperform")),
        ("supply-demand", ("capacity", "production", "supply", "shortage", "demand", "pricing pressure", "competitor")),
        ("product", ("launch", "unveil", "release", "model", "product", "chip", "platform", "contract", "order", "partnership")),
        ("macro", ("federal reserve", "fed ", "inflation", "cpi", "jobs", "payroll", "unemployment", "interest rate", "treasury")),
        ("corporate", ("acquisition", "merger", "takeover", "deal", "buyback", "dividend", "ceo", "offering", "debt", "stake", "bankruptcy", "layoff", "restructur")),
    ]
    for label, words in rules:
        if any(w in t for w in words):
            return label
    return "unclear"


def _company_query_name(company: str, symbol: str) -> str:
    x = _clean(company or symbol)
    x = re.sub(r"\b(holdings?|incorporated|inc|corporation|corp|company|co|plc|ltd|limited|sa|se|ag|nv|group|class a|class b)\b\.?", " ", x, flags=re.I)
    x = re.sub(r"[^A-Za-z0-9&' -]+", " ", x)
    return re.sub(r"\s+", " ", x).strip(" ,-. ") or symbol


def _domain(url: str, fallback: str = "") -> str:
    try:
        host = (urlparse(str(url or "")).hostname or "").lower().removeprefix("www.")
        return host or str(fallback or "").lower().removeprefix("www.")
    except Exception:
        return str(fallback or "").lower().removeprefix("www.")


def _domain_matches(domain: str, target: str) -> bool:
    return domain == target or domain.endswith("." + target)


def source_allowed(url: str, supplied_domain: str = "") -> bool:
    if not re.match(r"^https?://", str(url or ""), flags=re.I):
        return False
    domain = _domain(url, supplied_domain)
    return bool(domain) and not any(_domain_matches(domain, b) for b in BLOCKED_NEWS_DOMAINS)


def _has_emoji(text: str) -> bool:
    return any(unicodedata.category(ch) == "So" for ch in str(text or ""))


def company_headline_relevant(headline: str, company: str, symbol: str) -> bool:
    h = _clean(headline)
    low = h.lower()
    if not h or _has_emoji(h):
        return False
    # Reject community/commentary snippets. This specifically prevents posts such
    # as "I live near Absci's headquarters..." from being treated as catalysts.
    if re.search(r"\b(i|i'm|i’ve|i've|my|me|we|we're|we’ve|we've|our)\b", low):
        return False
    if any(x in low for x in ("reddit", "stocktwits", "forum post", "message board", "should you buy", "is it time to buy", "my portfolio")):
        return False
    qname = _company_query_name(company, symbol)
    if qname and qname.lower() in low:
        return True
    if len(symbol) >= 3 and re.search(rf"(?<![A-Z0-9]){re.escape(symbol.upper())}(?![A-Z0-9])", h.upper()):
        return True
    ignore = {"the", "and", "class", "group", "holdings", "technology", "technologies", "systems", "international"}
    tokens = [t.lower() for t in re.findall(r"[A-Za-z0-9]+", qname) if len(t) >= 4 and t.lower() not in ignore]
    return bool(tokens and any(t in low for t in tokens[:3]))


def source_score(url: str, supplied_domain: str = "") -> int:
    domain = _domain(url, supplied_domain)
    score = 30
    for target, value in PREFERRED_NEWS_DOMAINS.items():
        if _domain_matches(domain, target):
            score = max(score, value)
    low = str(url or "").lower()
    if any(x in low for x in ("/investor", "/investors", "/newsroom", "/press-release", "/press_releases")):
        score = max(score, 85)
    return score


class FreeNewsCollector:
    """100% free public-source news collector: GDELT + SEC + official RSS feeds."""

    def __init__(self) -> None:
        self.timeout = float(os.getenv("FREE_NEWS_TIMEOUT", "12"))
        self.sec_user_agent = os.getenv("SEC_USER_AGENT", "").strip()
        self.store = EventStore()
        self._ticker_map: dict[str, dict] | None = None
        self._ticker_lock = asyncio.Lock()

    @property
    def sec_configured(self) -> bool:
        return bool(self.sec_user_agent and "@" in self.sec_user_agent)

    async def _client_get(self, url: str, *, params: dict | None = None, sec: bool = False) -> httpx.Response:
        headers = {"Accept": "application/json, application/rss+xml, application/xml, text/xml, */*"}
        if sec:
            if not self.sec_configured:
                raise RuntimeError("SEC_USER_AGENT is missing or does not contain a contact email")
            headers["User-Agent"] = self.sec_user_agent
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, headers=headers) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            return r

    async def company_identity(self, symbol: str) -> dict:
        """Return canonical SEC company identity when available; cached after first load."""
        if not self.sec_configured:
            return {}
        mapping = await self._ensure_ticker_map()
        return mapping.get(symbol.upper(), {})

    async def gdelt_company(self, symbol: str, company: str, *, limit: int = 12) -> list[dict]:
        query_name = _company_query_name(company, symbol)
        query = f'"{query_name}" sourcelang:english' if query_name and query_name.upper() != symbol.upper() else f'"{symbol}" sourcelang:english'

        async def fetch(timespan: str, maxrecords: int) -> list[dict]:
            r = await self._client_get(
                GDELT_URL,
                params={"query": query, "mode": "ArtList", "maxrecords": min(max(maxrecords * 3, 20), 50), "format": "json", "sort": "DateDesc", "timespan": timespan},
            )
            payload = r.json() if r.content else {}
            candidates: list[tuple[int, str, dict]] = []
            for a in payload.get("articles") or []:
                headline = _clean(a.get("title"))
                url = str(a.get("url") or "").strip()
                supplied_domain = _clean(a.get("domain") or "")
                if not company_headline_relevant(headline, company, symbol):
                    continue
                if not source_allowed(url, supplied_domain):
                    continue
                kind = _classify(headline)
                published = _iso_date(a.get("seendate"))
                item = {
                    "event_key": _event_key("GDELT", url, headline),
                    "published_at": published,
                    "type": kind,
                    "headline": headline,
                    "source": _domain(url, supplied_domain) or "Public source",
                    "source_url": url,
                    "impact": "neutral",
                    "specificity": "company",
                    "summary": "",
                    "why_it_matters": "",
                    "move_connection": "",
                    "symbol": symbol,
                    "company": company,
                    "source_quality": source_score(url, supplied_domain),
                }
                candidates.append((item["source_quality"], str(published or ""), item))
            candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
            return [x[2] for x in candidates[:maxrecords]]

        out = await fetch("7d", limit)
        if not out:
            out = await fetch("30d", min(limit, 8))
        self.store.upsert_many(out)
        return out

    async def _ensure_ticker_map(self) -> dict[str, dict]:
        if self._ticker_map is not None:
            return self._ticker_map
        async with self._ticker_lock:
            if self._ticker_map is not None:
                return self._ticker_map
            r = await self._client_get(SEC_TICKERS_URL, sec=True)
            raw = r.json()
            mapping: dict[str, dict] = {}
            for item in raw.values() if isinstance(raw, dict) else []:
                ticker = str(item.get("ticker") or "").upper()
                cik = item.get("cik_str")
                if ticker and cik is not None:
                    mapping[ticker] = {"cik": int(cik), "name": _clean(item.get("title") or "")}
            self._ticker_map = mapping
            return mapping

    async def sec_company(self, symbol: str, company: str, *, limit: int = 8) -> list[dict]:
        if not self.sec_configured:
            return []
        mapping = await self._ensure_ticker_map()
        identity = mapping.get(symbol.upper()) or {}
        cik = identity.get("cik")
        if not cik:
            return []
        company = identity.get("name") or company
        r = await self._client_get(SEC_SUBMISSIONS.format(cik=cik), sec=True)
        payload = r.json()
        recent = ((payload.get("filings") or {}).get("recent") or {})
        forms = recent.get("form") or []
        accessions = recent.get("accessionNumber") or []
        primary_docs = recent.get("primaryDocument") or []
        dates = recent.get("filingDate") or []
        descriptions = recent.get("primaryDocDescription") or []
        out: list[dict] = []
        for i, form in enumerate(forms):
            if form not in MATERIAL_FORMS:
                continue
            accession = accessions[i] if i < len(accessions) else ""
            primary = primary_docs[i] if i < len(primary_docs) else ""
            filing_date = dates[i] if i < len(dates) else None
            desc = _clean(descriptions[i] if i < len(descriptions) else "")
            if not accession or not primary:
                continue
            acc_clean = accession.replace("-", "")
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_clean}/{primary}"
            headline = f"{company} filed {form}" + (f" — {desc}" if desc else "")
            out.append({
                "event_key": _event_key("SEC", url, headline),
                "published_at": _iso_date(filing_date),
                "type": "regulatory" if form.startswith("8-K") or form == "6-K" else "earnings",
                "headline": headline,
                "source": "SEC EDGAR",
                "source_url": url,
                "impact": "neutral",
                "specificity": "company",
                "summary": "",
                "why_it_matters": "",
                "move_connection": "",
                "symbol": symbol,
                "company": company,
            })
            if len(out) >= limit:
                break
        self.store.upsert_many(out)
        return out

    async def official_feeds(self, *, limit_each: int = 10) -> list[dict]:
        async def fetch(source: str, url: str) -> list[dict]:
            try:
                r = await self._client_get(url)
                root = ET.fromstring(r.content)
            except Exception:
                return []
            items: list[dict] = []
            # RSS 2.0 and Atom-ish fallback.
            entries = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
            for entry in entries[:limit_each]:
                def tx(name: str) -> str:
                    node = entry.find(name)
                    if node is None:
                        node = entry.find(f"{{http://www.w3.org/2005/Atom}}{name}")
                    return _clean(node.text if node is not None else "")
                title = tx("title")
                link = tx("link")
                if not link:
                    node = entry.find("{http://www.w3.org/2005/Atom}link")
                    if node is not None:
                        link = node.attrib.get("href", "")
                published = tx("pubDate") or tx("updated") or tx("published")
                summary = tx("description") or tx("summary")
                if not title:
                    continue
                item = {
                    "event_key": _event_key(source, link, title),
                    "published_at": _iso_date(published),
                    "type": _classify(title, summary),
                    "headline": title,
                    "source": source,
                    "source_url": link,
                    "impact": "neutral",
                    "specificity": "macro",
                    "summary": summary or f"Official release from {source}.",
                    "why_it_matters": "Primary-source macro releases can move rates, currencies, sectors and the overall equity market.",
                    "move_connection": "Use the release time alongside the Broad Market tab to see whether the move coincided with this announcement.",
                    "symbol": "",
                    "company": "",
                }
                items.append(item)
            return items

        groups = await asyncio.gather(*(fetch(s, u) for s, u in OFFICIAL_FEEDS))
        out = [x for g in groups for x in g]
        self.store.upsert_many(out)
        return out

    async def market_feed(self, *, limit: int = 50) -> list[dict]:
        gdelt_query = '("stock market" OR "Federal Reserve" OR inflation OR earnings OR semiconductor) sourcelang:english'
        gdelt: list[dict] = []
        try:
            r = await self._client_get(
                GDELT_URL,
                params={"query": gdelt_query, "mode": "ArtList", "maxrecords": 35, "format": "json", "sort": "DateDesc", "timespan": "1d"},
            )
            for a in (r.json().get("articles") or [])[:35]:
                headline = _clean(a.get("title"))
                url = str(a.get("url") or "")
                if not headline:
                    continue
                gdelt.append({
                    "event_key": _event_key("GDELT", url, headline),
                    "published_at": _iso_date(a.get("seendate")),
                    "type": _classify(headline),
                    "headline": headline,
                    "source": _clean(a.get("domain") or "GDELT-indexed source"),
                    "source_url": url,
                    "impact": "neutral",
                    "specificity": "macro",
                    "summary": "Recent market-related coverage indexed by GDELT.",
                    "why_it_matters": "Broad market coverage can help explain cross-sector moves and shifts in risk appetite.",
                    "move_connection": "Compare the headline timestamp with the live index, sector and macro proxies in Broad Market.",
                    "symbol": "",
                    "company": "",
                })
        except Exception:
            gdelt = []
        official = await self.official_feeds(limit_each=8)
        self.store.upsert_many(gdelt)
        combined = gdelt + official + self.store.recent(limit=80)
        return _dedupe(combined)[: max(1, min(limit, 100))]


def _dedupe(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for item in sorted(items, key=lambda x: str(x.get("published_at") or ""), reverse=True):
        title_key = re.sub(r"\W+", " ", str(item.get("headline") or "").lower()).strip()[:180]
        key = str(item.get("source_url") or "").strip().lower() or title_key
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
