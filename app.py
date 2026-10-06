from __future__ import annotations
import asyncio
import hashlib
import hmac
import os
import time
from pathlib import Path
from typing import Any
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from services.ai_service import AIService
from services.market_stream import MarketState
from services.news_service import NewsService
from services.research_service import ResearchService
from services.screener_registry import load_screeners, set_enabled
from services.settings_service import SettingsService
from services.dip_engine import classify

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
app = FastAPI(title="Meridian Market Dashboard", version="3.1.0")
state = MarketState()
news = NewsService()
research = ResearchService(news, state)
settings = SettingsService()
ai = AIService()

MERIDIAN_PASSWORD = os.getenv("MERIDIAN_PASSWORD", "").strip()

def _auth_token() -> str:
    if not MERIDIAN_PASSWORD:
        return ""
    return hashlib.sha256(("meridian-cloud:" + MERIDIAN_PASSWORD).encode("utf-8")).hexdigest()

def _authorized_cookie(value: str | None) -> bool:
    expected = _auth_token()
    return not expected or bool(value and hmac.compare_digest(value, expected))

@app.middleware("http")
async def meridian_auth(request: Request, call_next):
    if not MERIDIAN_PASSWORD or request.url.path in {"/login", "/api/health"}:
        return await call_next(request)
    if _authorized_cookie(request.cookies.get("meridian_session")):
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Authentication required"}, status_code=401)
    return RedirectResponse("/login", status_code=303)

@app.get("/login", response_class=HTMLResponse)
def login_page() -> str:
    if not MERIDIAN_PASSWORD:
        return '<meta http-equiv="refresh" content="0;url=/">'
    return """<!doctype html><html><head><meta name=viewport content='width=device-width,initial-scale=1'><title>Meridian</title><style>body{font-family:system-ui;background:#0d1117;color:#e6edf3;display:grid;place-items:center;height:100vh;margin:0}.box{width:min(360px,86vw);padding:28px;border:1px solid #30363d;border-radius:16px;background:#161b22}input,button{box-sizing:border-box;width:100%;padding:12px;margin-top:12px;border-radius:9px;border:1px solid #30363d;background:#0d1117;color:#e6edf3}button{background:#238636;border:0;font-weight:700;cursor:pointer}.err{color:#ff7b72;min-height:20px}</style></head><body><div class=box><h2>Meridian</h2><p>Enter the dashboard password.</p><input id=p type=password autofocus placeholder='Password'><button id=b>Open Meridian</button><p id=e class=err></p></div><script>async function go(){let r=await fetch('/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:document.getElementById('p').value})});if(r.ok)location='/';else document.getElementById('e').textContent='Wrong password';}document.getElementById('b').onclick=go;document.getElementById('p').onkeydown=e=>{if(e.key==='Enter')go()};</script></body></html>"""

@app.post("/login")
async def login(request: Request):
    if not MERIDIAN_PASSWORD:
        return {"ok": True}
    try:
        body = await request.json()
    except Exception:
        body = {}
    supplied = str(body.get("password") or "")
    if not hmac.compare_digest(supplied, MERIDIAN_PASSWORD):
        return JSONResponse({"detail": "Wrong password"}, status_code=401)
    response = JSONResponse({"ok": True})
    response.set_cookie("meridian_session", _auth_token(), httponly=True, secure=(bool(os.getenv("RENDER")) or request.url.scheme == "https"), samesite="lax", max_age=60*60*24*30)
    return response

@app.post("/logout")
def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie("meridian_session")
    return response

class SettingsPatch(BaseModel):
    changes: dict[str, Any]

class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    symbol: str | None = None

class CustomizeRequest(BaseModel):
    request: str = Field(min_length=1, max_length=4000)

class ApplyProposal(BaseModel):
    proposal: dict[str, Any]

@app.on_event("startup")
async def start_market_stream() -> None:
    asyncio.create_task(state.run())


def visible_rows(rows: list[dict] | None = None) -> list[dict]:
    cfg = settings.get()
    rows = rows if rows is not None else state.snapshot()
    out = []
    for x in rows:
        if x.get("kind", "stock") == "market":
            continue
        if state.provider == "ibkr" and not x.get("history_ready"):
            continue
        dd = abs(float(x.get("drawdown", 0)))
        if dd < float(cfg["min_drawdown"]) or dd > float(cfg["max_drawdown"]):
            continue
        if int(x.get("severity", 0)) < int(cfg["min_severity"]):
            continue
        if float(x.get("price", 0)) < 1:
            continue
        out.append(x)
    return out[: int(cfg["max_live_rows"])]

def market_overview(rows: list[dict] | None = None) -> dict:
    rows = rows if rows is not None else state.snapshot()
    market = [x for x in rows if x.get("kind") == "market"]
    stocks = [x for x in rows if x.get("kind", "stock") == "stock"]
    groups: dict[str, list[dict]] = {}
    for row in market:
        groups.setdefault(str(row.get("market_group") or "Other"), []).append(row)
    order = {
        "SPY": 1, "QQQ": 2, "IWM": 3, "DIA": 4,
        "XLK": 1, "XLF": 2, "XLE": 3, "XLV": 4, "XLI": 5, "XLY": 6, "XLP": 7, "XLU": 8, "XLRE": 9, "XLC": 10, "XLB": 11,
        "TLT": 1, "HYG": 2, "GLD": 3, "USO": 4, "UUP": 5, "SOXX": 1,
    }
    for items in groups.values():
        items.sort(key=lambda x: order.get(x.get("symbol"), 99))
    pos = sum(1 for x in stocks if float(x.get("day", 0)) > 0)
    neg = sum(1 for x in stocks if float(x.get("day", 0)) < 0)
    flat = max(0, len(stocks) - pos - neg)
    avg_stock = sum(float(x.get("day", 0)) for x in stocks) / len(stocks) if stocks else 0.0
    idx = {x["symbol"]: x for x in groups.get("Indexes", [])}
    risk_vals = [float(idx[s].get("day", 0)) for s in ("SPY", "QQQ", "IWM") if s in idx]
    risk_avg = sum(risk_vals) / len(risk_vals) if risk_vals else 0.0
    risk = "Risk-on" if risk_avg >= 0.5 else ("Risk-off" if risk_avg <= -0.5 else "Mixed")
    by_symbol = {x["symbol"]: x for x in market}
    def move(sym: str) -> float | None:
        return float(by_symbol[sym].get("day", 0)) if sym in by_symbol else None
    signals = [
        {"label": "Risk mood", "value": risk, "detail": f"SPY/QQQ/IWM average {risk_avg:+.2f}%"},
        {"label": "Long bonds", "value": f"{move('TLT'):+.2f}%" if move('TLT') is not None else "—", "detail": "TLT up often coincides with lower long yields; TLT down with higher yields."},
        {"label": "Credit", "value": f"{move('HYG'):+.2f}%" if move('HYG') is not None else "—", "detail": "HYG is a simple risk/credit proxy, not a credit-spread calculation."},
        {"label": "Oil", "value": f"{move('USO'):+.2f}%" if move('USO') is not None else "—", "detail": "USO is used as an oil-price proxy."},
        {"label": "Dollar", "value": f"{move('UUP'):+.2f}%" if move('UUP') is not None else "—", "detail": "UUP is used as a broad US-dollar proxy."},
        {"label": "Gold", "value": f"{move('GLD'):+.2f}%" if move('GLD') is not None else "—", "detail": "GLD is used as a gold-price proxy."},
    ]
    sectors = sorted(groups.get("Sectors", []), key=lambda x: float(x.get("day", 0)), reverse=True)
    return {
        "updated_at": state.updated_at,
        "provider": state.provider,
        "status": state.status,
        "breadth": {"tracked": len(stocks), "positive": pos, "negative": neg, "flat": flat, "average_day": round(avg_stock, 3)},
        "signals": signals,
        "groups": groups,
        "sector_leaders": sectors[:3],
        "sector_laggards": list(reversed(sectors[-3:])),
        "stock_leaders": sorted(stocks, key=lambda x: float(x.get("day", 0)), reverse=True)[:5],
        "stock_laggards": sorted(stocks, key=lambda x: float(x.get("day", 0)))[:5],
    }

@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "mode": state.provider, "version": "3.1.0", "stream_version": state.version, "updated_at": state.updated_at, "market_status": state.status, "ai_configured": ai.configured, "news_provider": news.provider, "news_live": news.live, "news_status": news.status}

@app.get("/api/screeners")
def screeners(include_disabled: bool = False) -> list[dict]:
    return load_screeners(include_disabled=include_disabled)

@app.get("/api/settings")
def get_settings() -> dict:
    return {"settings": settings.get(), "ai_configured": ai.configured, "ai_model": ai.model, "market_provider": state.provider, "market_status": state.status, "news_provider": news.provider, "news_live": news.live, "news_status": news.status}

@app.put("/api/settings")
def update_settings(req: SettingsPatch) -> dict:
    try:
        return {"settings": settings.update(req.changes), "applied_live": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/settings/reset")
def reset_settings() -> dict:
    return {"settings": settings.reset(), "applied_live": True}

@app.get("/api/live")
def live() -> dict:
    snapshot = state.snapshot()
    return {"mode": state.provider, "updated_at": state.updated_at, "rows": visible_rows(snapshot), "settings": settings.get(), "market_status": state.status, "market_overview": market_overview(snapshot)}

@app.get("/api/market-overview")
def broad_market() -> dict:
    return market_overview()

@app.get("/api/mover-radar")
async def mover_radar(region: str = "all", refresh: bool = False) -> dict:
    try:
        return await state.mover_radar(region=region, force=refresh)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

@app.get("/api/mover-news")
async def mover_news(symbol: str, name: str = "", region: str = "US", refresh: bool = False) -> dict:
    symbol = symbol.strip().upper()[:32]
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol is required")
    row = {
        "symbol": symbol,
        "name": (name or symbol).strip()[:180],
        "sector": "Unknown",
        "radar_external": region.lower() != "us",
        "radar_fast": True,
    }
    try:
        bundle = await news.for_row(row, force_refresh=refresh)
        return {
            "symbol": symbol,
            "company_name": bundle.get("company_name") or row["name"],
            "reason": str(bundle.get("overall_cause") or "").strip(),
            "cause_type": bundle.get("cause_type") or "unclear",
            "items": (bundle.get("items") or [])[:3],
            "live": bool(bundle.get("live")),
            "searched_at": bundle.get("searched_at"),
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

@app.get("/api/news-feed")
async def news_feed(limit: int = 60, scope: str = "all", refresh: bool = False) -> dict:
    return await news.market_feed(limit=max(1, min(limit, 100)), scope=scope, force_refresh=refresh)

@app.get("/api/news/{symbol}")
async def symbol_news(symbol: str, refresh: bool = False) -> dict:
    row = state.get(symbol)
    if not row:
        raise HTTPException(status_code=404, detail="Unknown symbol")
    bundle = await news.for_row(classify(row), force_refresh=refresh)
    reason = str(bundle.get("overall_cause") or "").strip()
    if reason:
        row["cause"] = reason
        row["cause_type"] = bundle.get("cause_type") or "company"
        row["cause_verified"] = True
    else:
        row["cause"] = ""
        row["cause_type"] = "unclear"
        row["cause_verified"] = False
    return {"symbol": symbol.upper(), **bundle}

@app.get("/api/research-summary/{symbol}")
async def symbol_research_summary(symbol: str) -> dict:
    row = state.get(symbol)
    if not row:
        raise HTTPException(status_code=404, detail="Unknown symbol")
    if row.get("provider") in {"ibkr", "yahoo"} and not row.get("recovery_real"):
        asyncio.create_task(state.ensure_recovery(symbol))
    return research.build_local(row)

@app.get("/api/research/{symbol}")
async def symbol_research(symbol: str, refresh_news: bool = False) -> dict:
    row = state.get(symbol)
    if not row:
        raise HTTPException(status_code=404, detail="Unknown symbol")
    return await research.build(row, force_news=refresh_news)

@app.post("/api/ai/ask")
async def ai_ask(req: AskRequest) -> dict:
    if not ai.configured:
        raise HTTPException(status_code=503, detail="AI is not connected. Set OPENAI_API_KEY in .env or your environment, then restart the server.")
    try:
        if req.symbol:
            row = state.get(req.symbol)
            if not row:
                raise HTTPException(status_code=404, detail="Unknown symbol")
            context = await research.build(row)
            answer = await ai.research_answer(req.question, context)
        else:
            context = {"rows": visible_rows()[:30], "settings": settings.get(), "screeners": load_screeners(), "market_provider": state.provider}
            answer = await ai.dashboard_answer(req.question, context)
        return {"answer": answer, "model": ai.model}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

@app.post("/api/ai/customize/preview")
async def customize_preview(req: CustomizeRequest) -> dict:
    if not ai.configured:
        raise HTTPException(status_code=503, detail="AI is not connected. Set OPENAI_API_KEY in .env or your environment, then restart the server.")
    try:
        proposal = await ai.propose_customization(req.request, settings.get(), load_screeners(include_disabled=True))
        return {"proposal": proposal}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

@app.post("/api/ai/customize/apply")
def customize_apply(req: ApplyProposal) -> dict:
    proposal = req.proposal or {}
    before = {"settings": settings.get(), "screeners": load_screeners(include_disabled=True)}
    changed = []
    for action in proposal.get("actions", []):
        kind = action.get("type")
        if kind == "update_settings":
            try:
                settings.update(action.get("changes", {}))
                changed.append("settings")
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
        elif kind == "toggle_screener":
            try:
                set_enabled(str(action.get("id")), bool(action.get("enabled")))
                changed.append(f"screener:{action.get('id')}")
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported automatic action: {kind}")
    return {"ok": True, "changed": changed, "before": before, "settings": settings.get(), "screeners": load_screeners(include_disabled=True), "reload_required": bool(proposal.get("reload_required"))}

@app.websocket("/ws/live")
async def live_ws(ws: WebSocket) -> None:
    if MERIDIAN_PASSWORD and not _authorized_cookie(ws.cookies.get("meridian_session")):
        await ws.close(code=4401)
        return
    await ws.accept()
    try:
        while True:
            cfg = settings.get()
            snapshot = state.snapshot()
            await ws.send_json({"mode": state.provider, "updated_at": time.time(), "version": state.version, "rows": visible_rows(snapshot), "settings": cfg, "market_status": state.status, "market_overview": market_overview(snapshot)})
            try:
                # Client heartbeats count as inbound WebSocket activity on free hosting.
                await asyncio.wait_for(ws.receive_text(), timeout=int(cfg["push_ms"]) / 1000)
            except asyncio.TimeoutError:
                pass
    except WebSocketDisconnect:
        return

app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")

@app.get("/{path:path}")
def spa(path: str):
    candidate = ROOT / "static" / path
    if candidate.exists() and candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(ROOT / "static" / "index.html")
