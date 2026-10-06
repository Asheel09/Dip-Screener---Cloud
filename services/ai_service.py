from __future__ import annotations
import json
import os
import re
from typing import Any
import httpx

API_URL = "https://api.openai.com/v1/responses"


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
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            raise ValueError("The AI response did not contain a JSON object.")
        return json.loads(m.group(0))


class AIService:
    def __init__(self) -> None:
        self.model = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")

    @property
    def configured(self) -> bool:
        return bool(os.getenv("OPENAI_API_KEY"))

    async def _respond(self, prompt: str, *, max_output_tokens: int = 1200) -> str:
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY is not configured.")
        body = {
            "model": self.model,
            "input": prompt,
            "max_output_tokens": max_output_tokens,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(API_URL, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, json=body)
            if r.status_code >= 400:
                try:
                    detail = r.json().get("error", {}).get("message", r.text)
                except Exception:
                    detail = r.text
                raise RuntimeError(f"OpenAI API error: {detail}")
            return _extract_text(r.json())

    async def research_answer(self, question: str, research: dict) -> str:
        compact = {
            "symbol": research.get("symbol"), "name": research.get("name"), "sector": research.get("sector"), "description": research.get("description"),
            "price": research.get("price"), "drawdown": research.get("drawdown"), "severity": research.get("severity"),
            "cause": research.get("cause"), "cause_type": research.get("cause_type"), "catalyst_risk": research.get("catalyst_risk"),
            "recovery": research.get("recovery"), "news": research.get("news", [])[:8], "history": research.get("history", [])[:10],
            "data_mode": research.get("mode"), "news_live": research.get("news_live"), "news_provider": research.get("news_provider"), "note": research.get("note"),
        }
        prompt = f"""You are the research assistant inside a market dashboard. Answer the user's question using the supplied dashboard evidence. Be concise, evidence-first, and distinguish observed data from interpretation. Do not invent missing news, fundamentals, prices, or probabilities. If the dashboard is in demo/synthetic mode, say so clearly. Do not give a buy/sell command; explain the evidence so the user can decide.\n\nDASHBOARD DATA:\n{json.dumps(compact, default=str)}\n\nUSER QUESTION:\n{question}\n"""
        return await self._respond(prompt, max_output_tokens=1400)

    async def dashboard_answer(self, question: str, context: dict) -> str:
        prompt = f"""You are the assistant inside Meridian, a modular market dashboard. Answer questions about the current opportunity feed, screeners, news, and settings using only the provided dashboard context. Be concise. If live market/news providers are not connected, state that limitation.\n\nCONTEXT:\n{json.dumps(context, default=str)}\n\nQUESTION:\n{question}\n"""
        return await self._respond(prompt, max_output_tokens=1200)

    async def propose_customization(self, request: str, settings: dict, screeners: list[dict]) -> dict:
        allowed = {
            "push_ms": "integer 250-10000 milliseconds",
            "max_live_rows": "integer 10-1000",
            "min_drawdown": "number 0-50 percent",
            "max_drawdown": "number 5-95 percent",
            "min_severity": "integer 1-99",
            "compact_mode": "boolean",
            "show_news_preview": "boolean",
            "ai_web_search": "boolean (reserved; does not alter market-data provider)",
        }
        prompt = f"""Translate a user's dashboard customization request into a SAFE proposal. Return JSON only, no markdown. Never propose arbitrary code execution or file edits. Supported live actions are only: (1) update_settings using the allowed keys below, and (2) toggle_screener using an existing screener id and enabled boolean. Anything else must go into requires_code and must not be applied automatically.\n\nAllowed settings: {json.dumps(allowed)}\nCurrent settings: {json.dumps(settings)}\nExisting screeners: {json.dumps(screeners)}\nUser request: {request}\n\nReturn exactly this shape:\n{{"summary":"short summary","actions":[{{"type":"update_settings","changes":{{}}}} or {{"type":"toggle_screener","id":"...","enabled":true}}],"requires_code":["..."],"reload_required":false}}\n"""
        text = await self._respond(prompt, max_output_tokens=900)
        proposal = _json_from_text(text)
        proposal.setdefault("summary", "Customization proposal")
        proposal.setdefault("actions", [])
        proposal.setdefault("requires_code", [])
        proposal["reload_required"] = bool(proposal.get("requires_code"))
        return proposal
