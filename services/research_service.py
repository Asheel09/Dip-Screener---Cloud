from __future__ import annotations

from .dip_engine import classify
from .news_service import NewsService, _usable_description


class ResearchService:
    def __init__(self, news: NewsService, market_state=None) -> None:
        self.news = news
        self.market_state = market_state

    def build_local(self, row: dict) -> dict:
        """Fast local-only research payload. Never waits on web news or IBKR history."""
        x = classify(row)
        rec = x.get("recovery_evidence") or {}
        score = rec.get("score") if rec else x.get("recovery_score")
        recovery = {
            "label": rec.get("label") or x.get("recovery") or "Pending",
            "score": score,
            "samples": rec.get("samples", 0),
            "positive_12w": rec.get("positive_12w", 0),
            "recovered_75pct": rec.get("recovered_75pct", 0),
            "median_12w": rec.get("median_12w"),
            "median_excess": rec.get("median_excess"),
            "worst": rec.get("worst"),
            "median_further_downside": rec.get("median_further_downside"),
            "reason": rec.get("reason", "Historical evidence is still being calculated."),
        }
        cause = str(x.get("cause") or "").strip() if x.get("cause_verified") else ""
        return {
            "symbol": x["symbol"],
            "name": x["name"],
            "sector": x["sector"],
            "price": x["price"],
            "market_session": x.get("market_session", ""),
            "prior_close": x.get("prior_close"),
            "description": _usable_description(x.get("description")),
            "drawdown": x["drawdown"],
            "severity": x["severity"],
            "severity_label": x["severity_label"],
            "cause": cause,
            "cause_type": x.get("cause_type", "unclear") if cause else "unclear",
            "recovery": recovery,
            "news": [],
            "news_provider": self.news.provider,
            "news_live": self.news.live,
            "history": rec.get("history", []) if rec else [],
            "mode": "demo" if x.get("provider", "demo") == "demo" else "live",
            "provider": x.get("provider", "demo"),
            "note": (
                "Recovery evidence is calculated from real Yahoo five-year daily history and real SPY history. No synthetic comparable episodes are used."
                if x.get("provider") == "yahoo"
                else ("Recovery evidence is calculated from real IBKR five-year daily history and real SPY history. No synthetic comparable episodes are used." if x.get("provider") == "ibkr" else "Demo mode uses bundled sample data.")
            ),
        }

    async def build(self, row: dict, *, force_news: bool = False) -> dict:
        """Full research payload. Used by AI and explicit refresh; UI opens from build_local first."""
        base = self.build_local(row)
        x = classify(row)
        news_bundle = await self.news.for_row(x, force_refresh=force_news)
        news = news_bundle.get("items") or []
        cause = str(news_bundle.get("overall_cause") or "").strip()
        cause_type = news_bundle.get("cause_type") or "unclear"
        if cause:
            row["cause"] = cause
            row["cause_type"] = cause_type
            row["cause_verified"] = True
        else:
            row["cause"] = ""
            row["cause_type"] = "unclear"
            row["cause_verified"] = False

        base.update({
            "name": news_bundle.get("company_name") or base["name"],
            "description": news_bundle.get("description") or base["description"],
            "cause": cause,
            "cause_type": cause_type if cause else "unclear",
            "news": news[:6],
            "news_provider": news_bundle.get("provider"),
            "news_live": bool(news_bundle.get("live")),
            "news_searched_at": news_bundle.get("searched_at"),
        })
        return base
