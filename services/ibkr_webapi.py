from __future__ import annotations

import asyncio
import json
import math
import os
import re
import statistics
import time
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import websocket

ROOT = Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT / "data" / "ibkr_contract_cache.json"


def _number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "")
    if not s or s in {"--", "N/A", "nan"}:
        return None
    mult = 1.0
    if s[-1:].upper() == "K":
        mult, s = 1_000.0, s[:-1]
    elif s[-1:].upper() == "M":
        mult, s = 1_000_000.0, s[:-1]
    elif s[-1:].upper() == "B":
        mult, s = 1_000_000_000.0, s[:-1]
    m = re.search(r"[-+]?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0)) * mult
    except ValueError:
        return None


def market_status(code: str | None) -> str:
    if not code:
        return "UNKNOWN"
    c = str(code)[0].upper()
    return {"R":"REALTIME","D":"DELAYED","Z":"FROZEN","Y":"FROZEN_DELAYED","N":"NOT_SUBSCRIBED","O":"API_AGREEMENT_REQUIRED"}.get(c,"UNKNOWN")


def market_data_details(code: str | None) -> dict[str, Any]:
    raw = str(code or "")
    status = market_status(raw)
    consolidated = len(raw) > 1 and raw[1] == "p"
    snapshot_available = len(raw) > 1 and raw[1] == "P"
    book = len(raw) > 2 and raw[2] == "B"
    if status == "REALTIME":
        label = f"REALTIME · {'CONSOLIDATED' if consolidated else 'NON-CONSOLIDATED'}"
    elif status == "DELAYED":
        label = "DELAYED"
    else:
        label = status
    return {"code":raw,"status":status,"consolidated":consolidated,"snapshot_available":snapshot_available,"book":book,"label":label}


@dataclass
class ContractRef:
    symbol: str
    conid: int
    exchange: str = "SMART"


class IBKRWebAPI:
    """Read-only Client Portal Gateway adapter with rotating snapshot support."""

    FIELDS = "31,70,71,83,87,7762,7741,6509,7051,7057,7058,7068"

    def __init__(self) -> None:
        self.base = os.getenv("IBKR_GATEWAY_URL", "https://localhost:5000/v1/api").rstrip("/")
        self.poll_ms = max(250, int(os.getenv("IBKR_POLL_MS", "500")))
        self.timeout = float(os.getenv("IBKR_HTTP_TIMEOUT", "10"))
        self.snapshot_warmup_ms = max(350, int(os.getenv("IBKR_SNAPSHOT_WARMUP_MS", "600")))
        self.quote_mode = os.getenv("IBKR_QUOTE_MODE", "free_stream").lower()
        self.free_exchange = os.getenv("IBKR_FREE_EXCHANGE", "IEX").upper()
        self.ws_collect_seconds = max(0.8, float(os.getenv("IBKR_WS_COLLECT_SECONDS", "1.6")))
        self.client = httpx.AsyncClient(base_url=self.base, verify=False, timeout=self.timeout)
        self.contracts: dict[str, ContractRef] = {}
        self.last_tickle = 0.0
        self.session_token: str | None = None
        self._scanner_params: tuple[float, dict] | None = None
        self._load_contract_cache()

    def _load_contract_cache(self) -> None:
        try:
            data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            for sym, conid in data.items():
                self.contracts[sym] = ContractRef(sym, int(conid))
        except Exception:
            pass

    def _save_contract_cache(self) -> None:
        try:
            CACHE_PATH.write_text(json.dumps({s:r.conid for s,r in self.contracts.items()}, indent=2), encoding="utf-8")
        except Exception:
            pass

    async def close(self) -> None:
        await self.client.aclose()

    async def auth_status(self) -> dict:
        r = await self.client.post("/iserver/auth/status", json={})
        r.raise_for_status()
        data = r.json()
        return data.get("success", {}).get("value", data) if isinstance(data, dict) else {}

    async def ensure_session(self) -> dict:
        status = await self.auth_status()
        if status.get("connected") and not status.get("authenticated"):
            try:
                r = await self.client.post("/iserver/auth/ssodh/init", json={"publish": True, "compete": False})
                if r.status_code < 400:
                    data = r.json(); status = data.get("success", {}).get("value", data) if isinstance(data, dict) else status
            except Exception:
                pass
        if not status.get("authenticated"):
            raise RuntimeError("IBKR Client Portal Gateway is reachable but the brokerage session is not authenticated. Open https://localhost:5000 in your browser and log in, then refresh the dashboard.")
        r = await self.client.get("/iserver/accounts"); r.raise_for_status()
        return status

    async def tickle(self) -> str | None:
        if time.time() - self.last_tickle < 55 and self.session_token:
            return self.session_token
        r = await self.client.post("/tickle", json={})
        r.raise_for_status()
        self.last_tickle = time.time()
        try:
            data = r.json() or {}
            token = data.get("session") if isinstance(data, dict) else None
            if token:
                self.session_token = str(token)
        except Exception:
            pass
        return self.session_token

    async def resolve(self, symbol: str) -> ContractRef:
        symbol = symbol.upper()
        if symbol in self.contracts:
            return self.contracts[symbol]
        # IBKR represents US share classes such as BRK.B/BF.B with a space.
        lookup_symbol = symbol.replace(".", " ") if "." in symbol else symbol
        r = await self.client.get("/iserver/secdef/search", params={"symbol": lookup_symbol})
        r.raise_for_status(); results = r.json() or []
        exact=[]
        for item in results:
            if str(item.get("symbol","")).upper() != lookup_symbol:
                continue
            sections=item.get("sections") or []
            if not any(str(s.get("secType","")).upper()=="STK" for s in sections):
                continue
            exact.append(item)
        if not exact:
            raise RuntimeError(f"IBKR could not resolve stock contract for {symbol}")
        preferred={"NASDAQ","NYSE","AMEX","ARCA","NYSEARCA","BATS","IBIS","AEB"}
        item=next((x for x in exact if str(x.get("description","")).upper() in preferred), exact[0])
        ref=ContractRef(symbol=symbol, conid=int(item["conid"]))
        self.contracts[symbol]=ref; self._save_contract_cache(); return ref

    async def resolve_many(self, symbols: list[str]) -> dict[str,str]:
        errors={}
        for symbol in symbols:
            if symbol in self.contracts:
                continue
            try:
                await self.resolve(symbol)
            except Exception as e:
                errors[symbol]=str(e)
            await asyncio.sleep(0.08)
        return errors

    async def history_bars(self, symbol: str, period: str = "5y", *, outside_rth: bool = False) -> list[dict]:
        ref = await self.resolve(symbol)
        r = await self.client.get("/iserver/marketdata/history", params={
            "conid": ref.conid, "exchange": ref.exchange, "period": period, "bar": "1d",
            "outsideRth": "true" if outside_rth else "false", "source": "Last"
        })
        r.raise_for_status()
        data = r.json() or {}
        return [b for b in (data.get("data") or []) if b.get("c") is not None]

    async def history_features(self, symbol: str) -> dict[str,Any]:
        bars = await self.history_bars(symbol, "5y", outside_rth=False)
        if len(bars) < 6:
            raise RuntimeError(f"Not enough IBKR history returned for {symbol}")
        highs=[float(b["h"]) for b in bars if b.get("h") is not None]; closes=[float(b["c"]) for b in bars]
        vols=[float(b.get("v") or 0) for b in bars if float(b.get("v") or 0)>0]
        rets=[closes[i]/closes[i-1]-1 for i in range(max(1,len(closes)-61),len(closes)) if closes[i-1]>0]
        ann_vol=statistics.pstdev(rets)*math.sqrt(252) if len(rets)>=5 else .25
        return {"high13":max(highs[-66:]) if highs else max(closes),"week_ref":closes[-6] if len(closes)>=6 else closes[0],"avg_volume":statistics.mean(vols[-60:]) if vols else 0.0,"vol":max(.08,min(1.5,ann_vol)),"history_last":closes[-1],"bars":bars}

    def session_route(self) -> tuple[str, str]:
        """Return (session label, exchange) for US equity market data."""
        now = datetime.now(ZoneInfo("America/New_York"))
        wd = now.weekday()  # Mon=0..Sun=6
        mins = now.hour * 60 + now.minute
        # IBKR overnight: Sun 20:00 through Fri 03:50, with a 10-minute break before 04:00.
        overnight = (wd == 6 and mins >= 20*60) or (wd in {0,1,2,3} and (mins >= 20*60 or mins < 230)) or (wd == 4 and mins < 230)
        if overnight:
            return "OVERNIGHT", "OVERNIGHT"
        if wd <= 4 and 240 <= mins < 570:
            return "PREMARKET", os.getenv("IBKR_PREMARKET_EXCHANGE", "EDGX").upper()
        if wd <= 4 and 570 <= mins < 960:
            return "REGULAR", self.free_exchange
        if wd <= 4 and 960 <= mins < 1200:
            return "AFTERHOURS", os.getenv("IBKR_PREMARKET_EXCHANGE", "EDGX").upper()
        return "CLOSED", self.free_exchange

    async def unsubscribe_all(self) -> None:
        try:
            r=await self.client.get("/iserver/marketdata/unsubscribeall")
            if r.status_code >= 400 and r.status_code not in {404,405}:
                r.raise_for_status()
        except Exception:
            pass

    def _parse_payload(self, payload: list[dict], refs: list[ContractRef]) -> dict[str,dict]:
        conid_to_symbol={x.conid:x.symbol for x in refs}; quotes={}
        for item in payload or []:
            if not item: continue
            symbol=conid_to_symbol.get(int(item.get("conid",0)))
            if not symbol: continue
            availability=market_data_details(item.get("6509"))
            volume=_number(item.get("7762")); volume = volume if volume is not None else _number(item.get("87"))
            quotes[symbol]={"price":_number(item.get("31")),"day":_number(item.get("83")),"volume":volume,"prior_close":_number(item.get("7741")),"company_name":item.get("7051") or "","market_status":availability["status"],"market_data_code":availability["code"],"market_data_label":availability["label"],"market_data_consolidated":availability["consolidated"],"last_exchange":item.get("7058") or "","ask_exchange":item.get("7057") or "","bid_exchange":item.get("7068") or "","quote_updated_at":(item.get("_updated") or 0)/1000 if item.get("_updated") else time.time()}
        return quotes


    def _ws_url(self) -> str:
        base = self.base
        if base.startswith("https://"):
            base = "wss://" + base[len("https://"):]
        elif base.startswith("http://"):
            base = "ws://" + base[len("http://"):]
        return base.rstrip("/") + "/ws"

    def _quote_window_ws_sync(self, refs: list[ContractRef], exchange: str, session_token: str | None) -> tuple[dict[str,dict], str | None]:
        """Collect one short exchange-specific websocket window. Used for the free IEX feed."""
        ws = None
        quotes: dict[str,dict] = {}
        error = None
        try:
            ws = websocket.create_connection(
                self._ws_url(),
                timeout=1.0,
                sslopt={"cert_reqs": ssl.CERT_NONE},
                suppress_origin=True,
            )
            # Client Portal Gateway WebSockets are a separate connection from our
            # HTTP client. Authenticate the socket with the session returned by /tickle.
            if not session_token:
                return {}, "IBKR /tickle did not return a WebSocket session token"
            ws.send(json.dumps({"session": session_token}, separators=(",", ":")))
            authenticated = False
            auth_deadline = time.time() + 2.0
            while time.time() < auth_deadline:
                try:
                    raw = ws.recv()
                except Exception:
                    continue
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                if isinstance(msg, dict):
                    if msg.get("message") == "waiting for session":
                        ws.send(json.dumps({"session": session_token}, separators=(",", ":")))
                        continue
                    if msg.get("topic") == "sts" and bool((msg.get("args") or {}).get("authenticated")):
                        authenticated = True
                        break
            if not authenticated:
                return {}, "IBKR WebSocket session authentication was not confirmed"

            fields=["31","83","87","7762","7741","6509","7051","7057","7058","7068"]
            body=json.dumps({"fields":fields},separators=(",",":"))
            for ref in refs:
                ws.send(f"smd+{ref.conid}@{exchange}+{body}")
            deadline=time.time()+self.ws_collect_seconds
            while time.time()<deadline and len(quotes)<len(refs):
                try:
                    raw=ws.recv()
                except Exception:
                    continue
                try:
                    msg=json.loads(raw)
                except Exception:
                    continue
                items=msg if isinstance(msg,list) else [msg]
                parsed=self._parse_payload([x for x in items if isinstance(x,dict)], refs)
                for sym,q in parsed.items():
                    # Exchange-specific websocket quotes must actually advertise real-time.
                    if q.get("price") is not None:
                        q["market_data_source"] = exchange
                        quotes[sym]=q
            for ref in refs:
                try: ws.send(f"umd+{ref.conid}@{exchange}+{{}}")
                except Exception: pass
        except Exception as e:
            error=str(e)
        finally:
            try:
                if ws: ws.close()
            except Exception: pass
        return quotes,error

    async def quote_window_free_stream(self, symbols: list[str]) -> tuple[dict[str,dict],dict[str,str]]:
        """Try the complimentary exchange-specific stream first, then SMART snapshot for misses."""
        session_token = await self.tickle()
        errors=await self.resolve_many(symbols)
        refs=[self.contracts[s] for s in symbols if s in self.contracts][:100]
        if not refs:
            return {}, errors
        session_label, exchange = self.session_route()
        quotes, ws_error = await asyncio.to_thread(self._quote_window_ws_sync, refs, exchange, session_token)
        for q in quotes.values():
            q["market_session"] = session_label
            q["market_data_source"] = exchange
        # Only trust this path as the live solution when IBKR marks it real-time.
        live={s:q for s,q in quotes.items() if q.get("market_status")=="REALTIME"}
        missing=[r.symbol for r in refs if r.symbol not in live]
        if missing:
            fallback, fb_errors = await self._quote_window_snapshot(missing, release_after=True)
            # Prefer a genuine live IEX quote over SMART delayed; use SMART only where live was absent.
            for q in fallback.values():
                q.setdefault("market_session", session_label)
                q.setdefault("market_data_source", "SMART")
            merged=dict(fallback); merged.update(live)
            errors.update(fb_errors)
            if ws_error and not live:
                errors.setdefault("_free_stream", ws_error)
            return merged, errors
        await self.unsubscribe_all()
        return live, errors

    async def _quote_window_snapshot(self, symbols: list[str], *, release_after: bool=True) -> tuple[dict[str,dict],dict[str,str]]:
        await self.tickle()
        errors=await self.resolve_many(symbols)
        refs=[self.contracts[s] for s in symbols if s in self.contracts]
        if not refs: return {}, errors or {s:"No resolved IBKR contract" for s in symbols}
        refs=refs[:100]
        params={"conids":",".join(str(x.conid) for x in refs),"fields":self.FIELDS}
        try:
            pre=await self.client.get("/iserver/marketdata/snapshot", params=params); pre.raise_for_status()
            await asyncio.sleep(self.snapshot_warmup_ms/1000)
            r=await self.client.get("/iserver/marketdata/snapshot", params=params); r.raise_for_status()
            quotes=self._parse_payload(r.json() or [], refs)
            # Some fields can still arrive on the third pass; only do it when the second pass was sparse.
            if len(quotes) < max(1, len(refs)//2):
                await asyncio.sleep(.25)
                r2=await self.client.get("/iserver/marketdata/snapshot", params=params); r2.raise_for_status()
                quotes.update(self._parse_payload(r2.json() or [], refs))
            return quotes, errors
        except Exception as e:
            for ref in refs: errors.setdefault(ref.symbol,str(e))
            return {}, errors
        finally:
            if release_after:
                await self.unsubscribe_all()

    async def scanner_params(self) -> dict:
        """Return IBKR market-scanner parameters, cached to respect the 15-minute endpoint limit."""
        now = time.time()
        if self._scanner_params and now - self._scanner_params[0] < 15 * 60:
            return self._scanner_params[1]
        await self.tickle()
        r = await self.client.get("/iserver/scanner/params")
        r.raise_for_status()
        data = r.json() or {}
        self._scanner_params = (now, data)
        return data

    @staticmethod
    def _walk_locations(nodes: list[dict], path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], dict]]:
        out: list[tuple[tuple[str, ...], dict]] = []
        for node in nodes or []:
            here = path + (str(node.get("display_name") or ""),)
            out.append((here, node))
            out.extend(IBKRWebAPI._walk_locations(node.get("locations") or [], here))
        return out

    @staticmethod
    def _scanner_region_configs(params: dict, region: str) -> list[dict[str, str]]:
        region = region.lower().strip()
        tree = params.get("location_tree") or []
        if region == "us":
            return [{"region": "US", "instrument": "STK", "location": "STK.US.MAJOR", "label": "US major exchanges"}]

        if region == "europe":
            top = next((n for n in tree if "stock" in str(n.get("display_name", "")).lower() and ("europe" in str(n.get("display_name", "")).lower() or "european" in str(n.get("display_name", "")).lower())), None)
            if not top:
                top = next((n for n in tree if str(n.get("type") or "").upper() == "STOCK.EU"), None)
            instrument = str((top or {}).get("type") or "STOCK.EU")
            descendants = IBKRWebAPI._walk_locations((top or {}).get("locations") or []) if top else []
            # Prefer a genuine aggregate-Europe location if this parameter version exposes one.
            aggregate = next((node for path, node in descendants if str(node.get("type") or "").upper() in {"STK.EU", "STK.EU.MAJOR"} or "all europe" in str(node.get("display_name") or "").lower()), None)
            if aggregate:
                return [{"region": "Europe", "instrument": instrument, "location": str(aggregate.get("type")), "label": str(aggregate.get("display_name") or "Europe") }]
            # Otherwise scan the major European primary venues. This deliberately includes SBF (Paris)
            # so names such as Schneider Electric are discoverable even though they are not US-listed.
            priority = ("SBF", "IBIS", "LSE", "AEB", "EBS", "BVME")
            configs=[]
            for suffix in priority:
                found = next((node for path, node in descendants if str(node.get("type") or "").upper().endswith("."+suffix)), None)
                if found:
                    configs.append({"region": "Europe", "instrument": instrument, "location": str(found.get("type")), "label": str(found.get("display_name") or suffix)})
            if configs:
                return configs
            # Last-resort fallback for older payloads.
            return [{"region": "Europe", "instrument": instrument, "location": "STK.EU.IBIS", "label": "European stocks (IBIS fallback)"}]
        raise ValueError(f"Unsupported scanner region: {region}")

    @staticmethod
    def _scanner_type(params: dict, wanted: str = "losers") -> str:
        scans = params.get("scan_type_list") or []
        if wanted == "losers":
            exact = next((x for x in scans if str(x.get("display_name") or "").lower() == "top % losers"), None)
            fuzzy = next((x for x in scans if "loser" in str(x.get("display_name") or "").lower() and "%" in str(x.get("display_name") or "")), None)
            return str((exact or fuzzy or {}).get("code") or "TOP_PERC_LOSE")
        if wanted == "hot_price":
            fuzzy = next((x for x in scans if "hot" in str(x.get("display_name") or "").lower() and "price" in str(x.get("display_name") or "").lower()), None)
            return str((fuzzy or {}).get("code") or "HOT_BY_PRICE")
        return wanted

    @staticmethod
    def _scanner_filters(params: dict, instrument: str) -> list[dict]:
        """Use only filters advertised by this IBKR parameter payload."""
        inst = next((x for x in (params.get("instrument_list") or []) if str(x.get("type") or "") == instrument), {})
        allowed = set(inst.get("filters") or [])
        global_filters = params.get("filter_list") or []
        by_display = {str(x.get("display_name") or "").lower(): str(x.get("code") or "") for x in global_filters}
        filters: list[dict] = []
        if "priceAbove" in allowed:
            filters.append({"code": "priceAbove", "value": 5})
        cap_code = "marketCapAbove1e6" if "marketCapAbove1e6" in allowed else ""
        if not cap_code:
            for display, code in by_display.items():
                if "market cap" in display and "above" in display and code in allowed:
                    cap_code = code; break
        if cap_code:
            # Filter value is in USD millions for the *1e6 market-cap scanner fields.
            filters.append({"code": cap_code, "value": 1000})
        return filters

    async def run_mover_scan(self, region: str, *, limit: int = 35) -> dict:
        """Return large/liquid downside movers from IBKR's own global market scanner."""
        await self.ensure_session()
        params = await self.scanner_params()
        configs = self._scanner_region_configs(params, region)
        scan_type = self._scanner_type(params, "losers")
        filters = self._scanner_filters(params, configs[0]["instrument"])
        all_rows=[]; errors=[]; used_filters=filters
        for idx,cfg in enumerate(configs):
            body = {"instrument": cfg["instrument"], "location": cfg["location"], "type": scan_type, "filter": used_filters}
            try:
                r = await self.client.post("/iserver/scanner/run", json=body)
                if r.status_code >= 400 and used_filters:
                    await asyncio.sleep(1.05)
                    used_filters=[]
                    body["filter"]=[]
                    r = await self.client.post("/iserver/scanner/run", json=body)
                r.raise_for_status()
                payload = r.json() or {}
                raw = payload.get("contracts") or []
                blocked = (" ETF", "FUND", "PROSHARES", "ISHARES", "SPDR", "DIREXION", "ULTRAPRO", "LEVERAGED", "2X ", "3X ")
                for rank, item in enumerate(raw[:50], 1):
                    name=str(item.get("company_name") or item.get("contract_description_1") or item.get("symbol") or "").strip()
                    upper=f" {name.upper()} "
                    if any(x in upper for x in blocked):
                        continue
                    all_rows.append({
                        "rank": rank,
                        "symbol": str(item.get("symbol") or "").strip(),
                        "name": name,
                        "conid": int(item.get("con_id") or 0),
                        "exchange": str(item.get("listing_exchange") or cfg["label"] or ""),
                        "region": cfg["region"],
                        "move": _number(item.get("scan_data")),
                        "scanner_value": item.get("scan_data"),
                        "scanner_location": cfg["label"],
                    })
            except Exception as e:
                errors.append(f"{cfg['label']}: {e}")
            if idx < len(configs)-1:
                await asyncio.sleep(1.05)
        def mv(x):
            v=x.get("move")
            return float(v) if v is not None else 999.0
        all_rows.sort(key=mv)
        seen=set(); rows=[]
        for item in all_rows:
            key=item.get("conid") or f"{item.get('region')}:{item.get('symbol')}"
            if key in seen: continue
            seen.add(key); rows.append(item)
            if len(rows)>=max(1,min(limit,50)): break
        return {"region": configs[0]["region"], "locations": [x["label"] for x in configs], "scan_type": scan_type, "column": "Change", "rows": rows, "filters": used_filters, "errors": errors}

    async def quote_window(self, symbols: list[str], *, release_after: bool=True) -> tuple[dict[str,dict],dict[str,str]]:
        if self.quote_mode in {"free_stream","iex","websocket"}:
            return await self.quote_window_free_stream(symbols)
        return await self._quote_window_snapshot(symbols, release_after=release_after)
