from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import httpx

from .free_news import FreeNewsCollector, _dedupe

ROOT = Path(__file__).resolve().parents[1]
API_URL = "https://api.openai.com/v1/responses"


def _usable_description(value: Any) -> str:
    text = str(value or "").strip()
    low = text.lower()
    generic = (
        "tracked by meridian",
        "tracked by the dashboard",
        "part of meridian's broad",
        "broad large-cap us screening universe",
        "large publicly traded company tracked",
    )
    return "" if not text or any(x in low for x in generic) else text


def _is_recent(item: dict, *, days: int = 7) -> bool:
    raw = str(item.get("published_at") or "")
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        return False
    return dt >= datetime.now(timezone.utc) - timedelta(days=days)


def _fresh_reason(items: list[dict], *, hours: int = 72) -> tuple[str, str]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    for item in items:
        if str(item.get("source") or "").upper().startswith("SEC"):
            continue
        raw = str(item.get("published_at") or "")
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if dt < cutoff:
            continue
        kind = str(item.get("type") or "unclear")
        if kind == "unclear":
            continue
        headline = str(item.get("headline") or "").strip()
        if headline:
            return headline[:180], kind
    return "No verified catalyst found", "unverified"


def _extract_text(payload: dict) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"].strip()
    chunks: list[str] = []
    for item in payload.get("output", []) or []:
        for part in item.get("content", []) or []:
            text = part.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "\n".join(chunks).strip()


def _json_from_text(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise ValueError("Web research did not return valid JSON.")
        return json.loads(match.group(0))


class NewsService:
    """Company profile + catalyst research.

    Default mode is fully free: GDELT + SEC EDGAR + official public RSS feeds.
    OpenAI web research remains optional, but is not required for live news.
    """

    def __init__(self) -> None:
        configured = bool(os.getenv("OPENAI_API_KEY"))
        requested = os.getenv("NEWS_PROVIDER", "free").lower().strip()
        if requested in {"auto", "free", "public", "gdelt"}:
            self.provider = "free-public"
        elif requested in {"openai", "openai-web", "web"}:
            self.provider = "openai-web" if configured else "free-public"
        elif requested == "demo":
            self.provider = "demo"
        else:
            self.provider = requested
        self.model = os.getenv("NEWS_MODEL", os.getenv("OPENAI_MODEL", "gpt-5.6-luna"))
        self.cache_seconds = int(os.getenv("RESEARCH_CACHE_SECONDS", "900"))
        self._cache: dict[str, tuple[float, dict]] = {}
        self._market_cache: tuple[float, list[dict]] | None = None
        self._demo = json.loads((ROOT / "data" / "demo_news.json").read_text(encoding="utf-8"))
        self.free = FreeNewsCollector()

    @property
    def live(self) -> bool:
        if self.provider == "free-public":
            return True
        return self.provider == "openai-web" and bool(os.getenv("OPENAI_API_KEY"))

    @property
    def status(self) -> dict:
        return {
            "provider": self.provider,
            "live": self.live,
            "sources": ["GDELT", "SEC EDGAR", "Federal Reserve", "BLS"] if self.provider == "free-public" else [self.provider],
            "sec_configured": self.free.sec_configured,
        }

    async def for_row(self, row: dict, *, force_refresh: bool = False) -> dict:
        symbol = str(row.get("symbol", "")).upper()
        name = str(row.get("name") or symbol).strip()
        # External/global scanner symbols can collide with US tickers (e.g. SU).
        cache_key = f"{symbol}|{name}" if row.get("radar_external") else symbol
        cached = self._cache.get(cache_key)
        now = time.time()
        if not force_refresh and cached and now - cached[0] < self.cache_seconds:
            return cached[1]

        if self.provider == "free-public":
            try:
                bundle = await self._free_bundle(row)
            except Exception as exc:
                bundle = self._fallback_bundle(row, error=str(exc))
        elif self.provider == "openai-web" and self.live:
            try:
                bundle = await self._openai_web_bundle(row)
            except Exception as exc:
                bundle = self._fallback_bundle(row, error=str(exc))
        else:
            bundle = self._fallback_bundle(row)

        self._cache[cache_key] = (now, bundle)
        return bundle

    async def for_symbol(self, symbol: str) -> list[dict]:
        # Compatibility path for older callers. It cannot provide a rich company
        # profile because no row/name metadata was supplied.
        row = {"symbol": symbol.upper(), "name": symbol.upper(), "sector": "Unknown"}
        return (await self.for_row(row))["items"]

    def _fallback_bundle(self, row: dict, error: str | None = None) -> dict:
        symbol = str(row.get("symbol", "")).upper()
        description = _usable_description(row.get("description"))
        items = self._demo.get(symbol, [{
            "published_at": None,
            "time": "--:--",
            "type": "unclear",
            "headline": "No clear material catalyst identified in demo mode",
            "source": "Scanner",
            "source_url": "",
            "impact": "neutral",
            "specificity": "unclear",
            "summary": "A live web news search is not active, so the dashboard cannot yet verify the current catalyst.",
            "why_it_matters": "The stock remains in the dip feed even when no explanation is available.",
            "move_connection": "Unverified until live news research is connected."
        }])
        normalized = [self._normalize_item(x) for x in items]
        return {
            "description": description,
            "overall_cause": "No verified catalyst found",
            "cause_type": "unverified",
            "items": normalized,
            "provider": "demo",
            "live": False,
            "searched_at": datetime.now(timezone.utc).isoformat(),
            "error": error,
        }

    async def market_feed(self, *, limit: int = 60, scope: str = "all", force_refresh: bool = False) -> dict:
        scope = scope if scope in {"all", "macro", "stocks"} else "all"
        now = time.time()
        items: list[dict]
        if not force_refresh and self._market_cache and now - self._market_cache[0] < 300:
            items = self._market_cache[1]
        elif self.provider == "free-public":
            try:
                items = [self._normalize_item(x) for x in await self.free.market_feed(limit=100)]
                items = [x for x in items if _is_recent(x, days=7)]
                self._market_cache = (now, items)
            except Exception as exc:
                cached = self._market_cache[1] if self._market_cache else []
                return {"items": cached[:limit], "provider": self.provider, "live": False, "cached": bool(cached), "status": {**self.status, "error": str(exc)}}
        else:
            items = []

        if scope == "macro":
            selected = [x for x in items if x.get("specificity") == "macro" or not x.get("symbol")]
        elif scope == "stocks":
            selected = [x for x in items if x.get("symbol")]
        else:
            selected = items
        return {"items": selected[: max(1, min(limit, 100))], "provider": self.provider, "live": self.live, "cached": bool(self._market_cache), "status": self.status}

    async def _free_bundle(self, row: dict) -> dict:
        symbol = str(row.get("symbol", "")).upper()
        supplied_name = str(row.get("name") or symbol)
        external = bool(row.get("radar_external"))
        fast = bool(row.get("radar_fast"))
        identity = {}
        if not external and not fast:
            try:
                identity = await self.free.company_identity(symbol)
            except Exception:
                identity = {}
        name = str(identity.get("name") or supplied_name or symbol).strip()
        if external or fast:
            gdelt_result = await self.free.gdelt_company(symbol, name)
            sec_result = []
        else:
            gdelt_result, sec_result = await __import__('asyncio').gather(
                self.free.gdelt_company(symbol, name),
                self.free.sec_company(symbol, name),
                return_exceptions=True,
            )
        errors: list[str] = []
        gdelt_items: list[dict] = []
        sec_items: list[dict] = []
        if isinstance(gdelt_result, Exception):
            errors.append(str(gdelt_result))
        else:
            gdelt_items = gdelt_result
        if isinstance(sec_result, Exception):
            errors.append(str(sec_result))
        else:
            sec_items = sec_result

        # Web headlines are the primary company-news surface. SEC filings are shown only
        # when extremely recent and never treated as proof of the reason for a move.
        cutoff = datetime.now(timezone.utc) - timedelta(days=3)
        recent_sec: list[dict] = []
        for item in sec_items:
            raw = str(item.get("published_at") or "")
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if dt >= cutoff:
                recent_sec.append(item)
        items = _dedupe(gdelt_items[:8] + recent_sec[:1])[:6]
        normalized = [self._normalize_item(x) for x in items]
        reason, reason_type = _fresh_reason(items)
        return {
            "description": _usable_description(row.get("description")),
            "company_name": name,
            "overall_cause": reason,
            "cause_type": reason_type,
            "items": normalized,
            "provider": "free-public",
            "live": True,
            "searched_at": datetime.now(timezone.utc).isoformat(),
            "error": "; ".join(errors) or None,
        }

    async def _openai_web_bundle(self, row: dict) -> dict:
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY is not configured")

        symbol = str(row.get("symbol", "")).upper()
        name = row.get("name") or symbol
        sector = row.get("sector") or "Unknown"
        today = datetime.now(timezone.utc).date().isoformat()
        context = {
            "symbol": symbol,
            "company": name,
            "sector": sector,
            "price": row.get("price"),
            "day_change_pct": row.get("day"),
            "week_change_pct": row.get("week"),
            "drawdown_from_13w_high_pct": row.get("drawdown"),
            "scanner_hint": row.get("cause"),
        }
        prompt = f"""Today is {today}. Research {name} ({symbol}) for a live market dashboard using web search.

PRICE CONTEXT FROM THE DASHBOARD:
{json.dumps(context, default=str)}

Return JSON only, with no markdown and no commentary outside the JSON. Use current, reputable sources. Prioritize company filings/IR, major financial news outlets, exchanges, regulators, and well-established technology/business publications. Do not invent a cause when the evidence is unclear.

Required shape:
{{
  "description": "2-3 sentence plain-English description of what the company does, its major businesses/products and where it competes",
  "overall_cause": "one concise best-supported explanation for the current move, or 'No clear material catalyst identified'",
  "cause_type": "company|sector|macro|second-order|unclear",
  "items": [
    {{
      "published_at": "ISO timestamp/date when available, otherwise null",
      "type": "earnings|guidance|product|regulatory|macro|sector|competitive|analyst-action|corporate|unclear",
      "headline": "specific headline",
      "source": "publisher/source name",
      "source_url": "exact source URL from the web search, otherwise empty string",
      "impact": "negative|positive|mixed|neutral",
      "specificity": "company|sector|macro|second-order|unclear",
      "summary": "2-4 sentences explaining what happened with concrete details, figures or dates when available",
      "why_it_matters": "1-3 sentences explaining why investors would care",
      "move_connection": "1-2 sentences explaining how directly this item plausibly relates to the observed price move; state when causality is uncertain"
    }}
  ]
}}

Include up to 6 genuinely relevant items, newest and most explanatory first. Avoid filling the list with weak duplicates. If no clear current news explains the decline, still return useful recent company/sector context and say clearly that the direct cause is uncertain."""

        body = {
            "model": self.model,
            "tools": [{"type": "web_search", "search_context_size": "medium"}],
            "input": prompt,
            "max_output_tokens": 3200,
        }
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                API_URL,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=body,
            )
        if response.status_code >= 400:
            try:
                detail = response.json().get("error", {}).get("message", response.text)
            except Exception:
                detail = response.text
            raise RuntimeError(f"OpenAI web search error: {detail}")

        parsed = _json_from_text(_extract_text(response.json()))
        items = [self._normalize_item(x) for x in (parsed.get("items") or [])[:6]]
        if not items:
            items = [self._normalize_item({
                "type": "unclear",
                "headline": "No clear material catalyst identified",
                "source": "Web research",
                "summary": "The live search did not identify a sufficiently strong current catalyst to attribute the move confidently.",
                "why_it_matters": "An unexplained move can still be worth investigating, but the absence of a catalyst should not be treated as proof the decline is temporary.",
                "move_connection": "Direct causality remains unclear.",
            })]
        return {
            "description": parsed.get("description") or row.get("description") or f"{name} operates in {sector}.",
            "overall_cause": parsed.get("overall_cause") or "No clear material catalyst identified",
            "cause_type": parsed.get("cause_type") or "unclear",
            "items": items,
            "provider": "openai-web",
            "live": True,
            "searched_at": datetime.now(timezone.utc).isoformat(),
            "error": None,
        }

    @staticmethod
    def _normalize_item(item: dict) -> dict:
        published = item.get("published_at")
        time_label = item.get("time") or (str(published)[:16].replace("T", " ") if published else "Recent")
        return {
            "published_at": published,
            "time": time_label,
            "type": item.get("type") or "unclear",
            "headline": item.get("headline") or "Untitled catalyst",
            "source": item.get("source") or "Unknown source",
            "source_url": item.get("source_url") or item.get("url") or "",
            "impact": item.get("impact") or "neutral",
            "specificity": item.get("specificity") or "unclear",
            "summary": item.get("summary") or "No summary available.",
            "why_it_matters": item.get("why_it_matters") or "",
            "move_connection": item.get("move_connection") or "",
        }
