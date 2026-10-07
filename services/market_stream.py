from __future__ import annotations
import asyncio, copy, json, os, random, time
from pathlib import Path
from .dip_engine import classify
from .ibkr_webapi import IBKRWebAPI
from .yahoo_market import YahooMarketAPI
from .tradingview_market import TradingViewMoverAPI
from .recovery_engine import analyse_recovery
from .free_news import source_allowed, company_headline_relevant

ROOT=Path(__file__).resolve().parents[1]
HISTORY_CACHE=ROOT/'data'/'market_history_cache.json'
DEFAULT_PRIORITY=['AMD','NVDA','AAPL','MSFT','GOOGL','GOOG','AMZN','META','AVGO','TSLA','AMAT','ASML','MU','ORCL','BKNG','UBER','JPM','CAT','LVS','STAG','NKE','PLTR','QCOM','INTC','MRVL']

class MarketState:
    def __init__(self)->None:
        self.provider=os.getenv('MARKET_DATA_PROVIDER','demo').lower()
        base_rows=json.loads((ROOT/'data'/'demo_universe.json').read_text(encoding='utf-8'))
        self.rows={r['symbol']:{**r,'provider':self.provider} for r in base_rows}
        if self.provider in {'ibkr','yahoo'}:
            for row in self.rows.values():
                row['cause']=''
                row['cause_type']='unclear'
                row['cause_verified']=False
        self.version=0; self.updated_at=time.time(); self._rng=random.Random(42)
        self.ready_symbols=set(self.rows) if self.provider=='demo' else set()
        self.connection_state='DEMO' if self.provider=='demo' else 'WAITING'
        self.connection_message='Synthetic market stream' if self.provider=='demo' else ('Starting cloud market data' if self.provider=='yahoo' else 'Waiting for IBKR Client Portal Gateway')
        self.last_error=None; self._ibkr=None; self._yahoo=None; self._tradingview=TradingViewMoverAPI() if self.provider=='yahoo' else None; self._history={}; self._spy_bars=None; self.rotation_index=0; self.scan_cycle=0; self.scanned_symbols=set(); self._history_task=None; self._mover_cache={}; self._relay_movers=None
        self.market_symbols=[s for s,r in self.rows.items() if r.get('kind')=='market']
        self.stock_symbols=[s for s,r in self.rows.items() if r.get('kind','stock')=='stock']
        requested=[s.strip().upper() for s in os.getenv('MERIDIAN_PRIORITY_SYMBOLS',','.join(DEFAULT_PRIORITY)).split(',') if s.strip()]
        self.priority_symbols=[s for s in requested if s in self.rows and s in self.stock_symbols]
        self.active_lines=max(50,min(100,int(os.getenv('IBKR_ACTIVE_LINES','90'))))
        if self.provider in {'ibkr','yahoo'}:
            for row in self.rows.values():
                if row.get('kind','stock') == 'stock':
                    row['recovery']='Pending'; row['recovery_score']=None; row['recovery_real']=False
        self.core_symbols=[]
        for s in self.market_symbols+self.priority_symbols:
            if s not in self.core_symbols:
                self.core_symbols.append(s)
        self._load_history_cache()

    def _load_history_cache(self):
        try:
            payload=json.loads(HISTORY_CACHE.read_text(encoding='utf-8'))
            if time.time()-float(payload.get('saved_at',0))>7*24*3600: return
            for s,feat in (payload.get('features') or {}).items():
                if s in self.rows:
                    self._history[s]=feat; self._apply_history(s,feat)
        except Exception: pass

    def _save_history_cache(self):
        try: HISTORY_CACHE.write_text(json.dumps({'saved_at':time.time(),'features':self._history},indent=2),encoding='utf-8')
        except Exception: pass

    @property
    def status(self)->dict:
        status_counts={}; modes={}
        for s in self.ready_symbols:
            st=str(self.rows[s].get('market_status','UNKNOWN')); status_counts[st]=status_counts.get(st,0)+1
            mode=str(self.rows[s].get('market_data_label',st)); modes[mode]=modes.get(mode,0)+1
        hist=sum(1 for s in self.stock_symbols if self.rows[s].get('history_ready'))
        if self._ibkr:
            current_session=self._ibkr.session_route()[0]
        elif self._yahoo:
            current_session=self._yahoo.us_session()
        else:
            current_session='DEMO' if self.provider=='demo' else 'UNKNOWN'
        return {'provider':self.provider,'state':self.connection_state,'message':self.connection_message,'last_error':self.last_error,'ready_symbols':len(self.ready_symbols),'total_symbols':len(self.rows),'tracked_stocks':len(self.stock_symbols),'active_line_budget':self.active_lines if self.provider=='ibkr' else None,'scan_cycle':self.scan_cycle,'scanned_symbols':len(self.scanned_symbols),'history_ready':hist,'current_session':current_session,'market_status_counts':status_counts,'market_data_modes':modes}

    def snapshot(self)->list[dict]:
        symbols=self.rows.keys() if self.provider=='demo' else self.ready_symbols
        rows=[classify(copy.deepcopy(self.rows[s])) for s in symbols]
        rows.sort(key=lambda x:(x['severity'],abs(x['day'])),reverse=True); return rows

    def get(self,symbol):
        symbol=symbol.upper()
        if self.provider!='demo' and symbol not in self.ready_symbols:return None
        return self.rows.get(symbol)

    async def run(self):
        if self.provider=='demo': await self.demo_tick()
        elif self.provider=='ibkr': await self.ibkr_tick()
        elif self.provider=='yahoo': await self.yahoo_tick()
        else: self.connection_state='ERROR'; self.connection_message=f'Unknown market provider: {self.provider}'; self.last_error=self.connection_message

    async def demo_tick(self):
        while True:
            for row in self.rows.values():
                step=self._rng.gauss(0,row.get('vol',.25)/140); old=row['price']; new=max(1.0,old*(1+step)); row['price']=round(new,2); row['day']=round(row['day']+(new/old-1)*100,2); row['week']=round(row['week']+(new/old-1)*45,2); row['volume_ratio']=round(max(.45,row['volume_ratio']+self._rng.gauss(0,.025)),2)
            self.version+=1; self.updated_at=time.time(); await asyncio.sleep(.5)

    async def _prepare_ibkr(self):
        self.connection_state='CONNECTING'; self.connection_message='Connecting to IBKR Client Portal Gateway'; await self._ibkr.ensure_session()
        self.connection_message=f'Preparing rotating scan for {len(self.stock_symbols)} stocks + {len(self.market_symbols)} market proxies'

    def _next_window(self):
        fixed=[]
        for s in self.market_symbols+self.priority_symbols:
            if s not in fixed: fixed.append(s)
        capacity=max(1,self.active_lines-len(fixed))
        rotating=[s for s in self.stock_symbols if s not in self.priority_symbols]
        if not rotating:return fixed[:self.active_lines]
        batch=[]
        for _ in range(capacity):
            if self.rotation_index>=len(rotating): self.rotation_index=0; self.scan_cycle+=1
            batch.append(rotating[self.rotation_index]); self.rotation_index+=1
        return (fixed+batch)[:self.active_lines]

    def _apply_history(self,symbol,feat):
        row=self.rows[symbol]; row['high13']=feat['high13']; row['vol']=feat['vol']; row['_week_ref']=feat['week_ref']; row['_avg_volume']=feat['avg_volume']; row['history_ready']=True
        rec=feat.get('recovery') or {}
        if rec:
            row['recovery']=rec.get('label','Insufficient'); row['recovery_score']=rec.get('score'); row['recovery_real']=True
            row['recovery_evidence']=rec

    async def ensure_recovery(self, symbol: str) -> dict:
        symbol=symbol.upper()
        row=self.rows.get(symbol)
        if not row:
            return {}
        existing=row.get('recovery_evidence')
        if existing and row.get('recovery_real'):
            return existing
        api=self._ibkr or self._yahoo
        if not api:
            return {}
        if not self._spy_bars:
            if self._ibkr:
                self._spy_bars=await self._ibkr.history_bars('SPY','5y',outside_rth=False)
            else:
                self._spy_bars=await self._yahoo.history_bars('SPY','5y')
        feat=await api.history_features(symbol)
        current_price=float(row.get('price') or feat.get('history_last') or 0)
        rec=analyse_recovery(feat.pop('bars',[]),self._spy_bars,current_price)
        feat['recovery']=rec
        self._history[symbol]=feat; self._apply_history(symbol,feat); self._save_history_cache(); self.version+=1
        return rec

    async def _history_worker(self):
        # One stock history request every ~1.4s (plus one SPY benchmark request at startup)
        # stays below IBKR's current 50 historical requests/minute cap. The same 5y request
        # now powers both 13-week live metrics and real recovery analogues.
        while True:
            try:
                if self._spy_bars is None:
                    try:
                        self._spy_bars=await self._ibkr.history_bars('SPY','5y',outside_rth=False)
                    except Exception:
                        self._spy_bars=[]
                    await asyncio.sleep(1.4)
                candidates=[s for s in self.stock_symbols if s in self.ready_symbols and not self.rows[s].get('recovery_real')]
                candidates.sort(key=lambda s:(s not in self.priority_symbols,-abs(float(self.rows[s].get('day',0)))))
                if candidates:
                    s=candidates[0]
                    try:
                        feat=await self._ibkr.history_features(s)
                        current_price=float(self.rows[s].get('price') or feat.get('history_last') or 0)
                        bars=feat.pop('bars',[])
                        feat['recovery']=analyse_recovery(bars,self._spy_bars or [],current_price)
                        self._history[s]=feat; self._apply_history(s,feat); self._save_history_cache(); self.version+=1
                    except Exception as e:
                        self.rows[s]['history_error']=str(e)
                await asyncio.sleep(1.4)
            except asyncio.CancelledError: raise
            except Exception:
                await asyncio.sleep(2)

    def _apply_quote(self,symbol,quote):
        row=self.rows[symbol]; price=quote.get('price')
        if price is None or price<=0:return False
        row['price']=round(float(price),4)
        if quote.get('day') is not None: row['day']=round(float(quote['day']),3)
        elif quote.get('prior_close'): row['day']=round((price/float(quote['prior_close'])-1)*100,3)
        week_ref=float(row.get('_week_ref') or 0)
        if week_ref>0: row['week']=round((price/week_ref-1)*100,3)
        volume=quote.get('volume'); avg=float(row.get('_avg_volume') or 0); row['volume_ratio']=round(max(0,float(volume)/avg),3) if volume is not None and avg>0 else 1.0
        if quote.get('company_name') and (row.get('name')==symbol or not row.get('name')): row['name']=quote['company_name']
        for k in ('market_status','market_data_code','market_data_label','market_data_consolidated','market_data_source','market_session','last_exchange','ask_exchange','bid_exchange','quote_updated_at','prior_close'):
            if k in quote: row[k]=quote[k]
        row['provider']='ibkr'; self.ready_symbols.add(symbol); self.scanned_symbols.add(symbol); return True

    def set_relay_movers(self, rows: list[dict], *, source: str = "local-relay", collected_at: float | None = None) -> dict:
        now=time.time(); clean=[]
        for raw in (rows or [])[:160]:
            try:
                symbol=str(raw.get('symbol') or '').strip()[:32]
                name=str(raw.get('name') or symbol).strip()[:180]
                region=str(raw.get('region') or 'US').strip()[:24]
                move=float(raw.get('move'))
            except Exception:
                continue
            if not symbol or not (-100 < move < 100):
                continue
            if not region.lower().startswith('us'):
                continue
            reason=str(raw.get('reason') or '').strip()[:180]
            news_url=str(raw.get('news_url') or '').strip()[:1200]
            verified=bool(raw.get('reason_verified'))
            # Do not blindly trust an older relay: enforce the same source and relevance
            # rules server-side before showing a headline as a verified reason.
            if verified and reason and reason != 'No verified catalyst found':
                verified = source_allowed(news_url, str(raw.get('news_source') or '')) and company_headline_relevant(reason, name, symbol)
            if not verified:
                reason='No verified catalyst found'
                news_url=''
            row={
                'symbol':symbol,'name':name,'region':region,
                'region_code':'us',
                'exchange':str(raw.get('exchange') or '')[:32],
                'move':round(move,3),'scanner_value':f'{move:.2f}%',
                'price':raw.get('price'),'extended_price':raw.get('extended_price'),
                'prior_close':raw.get('prior_close'),'session':str(raw.get('session') or '')[:32],
                'market_state':str(raw.get('market_state') or raw.get('session') or '')[:32],
                'delay_minutes':raw.get('delay_minutes'),'quote_source':str(raw.get('quote_source') or source)[:80],
                'currency':str(raw.get('currency') or '')[:12],'market_cap':raw.get('market_cap'),
                'volume':raw.get('volume'),'relative_volume':raw.get('relative_volume'),
                'tv_symbol':str(raw.get('tv_symbol') or '')[:80],
                'reason':reason,
                'cause_type':str(raw.get('cause_type') or 'unverified')[:40] if verified else 'unverified',
                'news_url':news_url,
                'news_source':str(raw.get('news_source') or '')[:120] if verified else '',
                'news_published_at':str(raw.get('news_published_at') or '')[:120] if verified else '',
                'reason_verified':verified,
                # Preserve the latest accepted company-news link even when it is
                # not strong enough to be promoted as the verified cause.
                'latest_news_headline':str(raw.get('latest_news_headline') or '')[:220],
                'latest_news_url':str(raw.get('latest_news_url') or '')[:1200],
                'latest_news_source':str(raw.get('latest_news_source') or '')[:120],
                'latest_news_published_at':str(raw.get('latest_news_published_at') or '')[:120],
            }
            clean.append(row)
        clean.sort(key=lambda x: abs(float(x.get('move') or 0)), reverse=True)
        for i,row in enumerate(clean,1): row['rank']=i
        ts=float(collected_at or now)
        self._relay_movers={'rows':clean,'source':source,'collected_at':ts,'received_at':now}
        self.version+=1
        return {'ok':True,'accepted':len(clean),'collected_at':ts}

    def _relay_scan(self, region: str) -> dict | None:
        relay=self._relay_movers
        if not relay: return None
        rows=relay.get('rows') or []
        rows=[x for x in rows if x.get('region_code')=='us']
        age=max(0,time.time()-float(relay.get('collected_at') or 0))
        stale=age>600
        return {
            'provider':'local-relay','region':region,'rows':rows[:60],
            'errors':(['Local relay snapshot is stale; start the Meridian relay on your Mac for fresh mover data.'] if stale else []),
            'updated_at':float(relay.get('collected_at') or 0),'received_at':float(relay.get('received_at') or 0),
            'age_seconds':round(age,1),'stale':stale,'cached':True,
            'session_note':'Mover data collected from the local Meridian relay and pushed securely to Render.'
        }

    async def mover_radar(self, region: str = "us", *, force: bool = False) -> dict:
        region=region.lower().strip()
        if region not in {"all","us"}:
            raise ValueError("Mover Radar is US-only in Meridian v3.4")
        region='us'
        if self.provider == "yahoo":
            relay=self._relay_scan(region)
            if relay is not None:
                return relay
            # Render/shared cloud IPs are often throttled by Yahoo's screener.
            # Use TradingView's bulk market scanner first; Yahoo remains a fallback.
            limit=50
            if not self._tradingview:
                self._tradingview=TradingViewMoverAPI()
            tv = await self._tradingview.run_mover_scan(region, limit=limit, force=force)
            if tv.get("rows"):
                return tv
            if not self._yahoo:
                self._yahoo=YahooMarketAPI()
            try:
                yh = await self._yahoo.run_mover_scan(region, limit=limit, force=force)
                if tv.get("errors"):
                    yh["errors"] = [*(tv.get("errors") or []), *(yh.get("errors") or [])]
                return yh
            except Exception as exc:
                tv["errors"] = [*(tv.get("errors") or []), f"Yahoo fallback: {exc}"]
                return tv
        if self.provider != "ibkr":
            rows=[x for x in self.snapshot() if x.get("kind","stock")=="stock"]
            rows=sorted(rows,key=lambda x:abs(float(x.get("day",0))),reverse=True)[:35]
            return {"provider":"demo","region":region,"rows":[{"rank":i+1,"symbol":x["symbol"],"name":x.get("name",x["symbol"]),"region":"US","move":x.get("day"),"exchange":"DEMO","conid":0,"reason":"No verified catalyst found","cause_type":"unverified","reason_verified":False} for i,x in enumerate(rows)],"cached":False,"updated_at":time.time()}
        if not self._ibkr:
            raise RuntimeError("IBKR is still connecting. Wait for the dashboard status to show connected, then refresh Mover Radar.")
        now=time.time(); cache=self._mover_cache.get(region)
        if not force and cache and now-cache[0] < 30:
            return {**cache[1],"cached":True}
        regions=['us']
        scans=[]; errors=[]
        for idx,r in enumerate(regions):
            try:
                scans.append(await self._ibkr.run_mover_scan(r,limit=35))
            except Exception as e:
                errors.append(f"{r}: {e}")
            if idx < len(regions)-1:
                await asyncio.sleep(1.05)  # scanner/run is limited to one request/second
        merged=[]
        for scan in scans:
            merged.extend(scan.get("rows") or [])
            errors.extend(scan.get("errors") or [])
        def mv(x):
            try:return float(x.get("move"))
            except Exception:return 999.0
        merged.sort(key=lambda x:abs(mv(x)),reverse=True)
        # De-duplicate by contract id first, then region+symbol.
        seen=set(); rows=[]
        for x in merged:
            key=x.get("conid") or f"{x.get('region')}:{x.get('symbol')}"
            if key in seen: continue
            seen.add(key); rows.append(x)
        result={"provider":"ibkr-scanner","region":region,"rows":rows[:60],"scans":scans,"errors":errors,"updated_at":now}
        self._mover_cache[region]=(now,result)
        return {**result,"cached":False}

    def _apply_yahoo_core(self, symbol: str, quote: dict) -> bool:
        row=self.rows.get(symbol)
        if not row:
            return False
        price=quote.get('price')
        if price is None or float(price)<=0:
            return False
        row['price']=round(float(price),4)
        row['prior_close']=quote.get('prior_close')
        row['day']=round(float(quote.get('day') or 0),3)
        row['week']=round(float(quote.get('week') or 0),3)
        row['high13']=float(quote.get('high13') or price)
        row['vol']=float(quote.get('vol') or .25)
        avg=float(quote.get('avg_volume') or 0); volume=float(quote.get('volume') or 0)
        row['volume_ratio']=round(volume/avg,3) if avg>0 else 1.0
        row['provider']='yahoo'
        session=self._yahoo.us_session() if self._yahoo else 'UNKNOWN'
        row['market_status']='REFERENCE' if session!='REGULAR' else 'DELAYED'
        row['market_data_label']='YAHOO · PREVIOUS CLOSE' if session!='REGULAR' else 'YAHOO · MARKET DATA'
        row['market_data_source']='Yahoo Finance'
        row['market_session']='PREVIOUS CLOSE' if session!='REGULAR' else 'REGULAR'
        row['quote_updated_at']=time.time()
        row['history_ready']=True
        self.ready_symbols.add(symbol); self.scanned_symbols.add(symbol)
        return True

    async def yahoo_tick(self):
        self._yahoo=YahooMarketAPI()
        self.connection_state='CONNECTING'; self.connection_message='Loading cloud market reference data'
        backoff=15.0
        while True:
            try:
                quotes=await self._yahoo.core_quotes(self.core_symbols)
                changed=sum(int(self._apply_yahoo_core(s,q)) for s,q in quotes.items())
                if changed:
                    self.version+=1; self.updated_at=time.time()
                self.connection_state='LIVE' if changed else ('CONNECTED' if self.ready_symbols else 'WAITING')
                sess=self._yahoo.us_session()
                self.connection_message=f'Cloud market data · {sess} · {len(self.ready_symbols)} core instruments ready · TradingView Movers available'
                self.last_error=None; backoff=15.0
                # Daily/reference core data does not need per-second polling. The browser
                # still receives WebSocket state; global mover scans cache for two minutes.
                await asyncio.sleep(max(60,int(os.getenv('YAHOO_CORE_REFRESH_SECONDS','300'))))
            except Exception as e:
                self.connection_state='OFFLINE'; self.connection_message='Cloud reference market data temporarily unavailable'; self.last_error=str(e)
                await asyncio.sleep(backoff); backoff=min(120,backoff*1.5)

    async def ibkr_tick(self):
        self._ibkr=IBKRWebAPI(); backoff=2.0
        try:
            await self._prepare_ibkr(); self._history_task=asyncio.create_task(self._history_worker())
            while True:
                try:
                    window=self._next_window(); quotes,errors=await self._ibkr.quote_window(window,release_after=True)
                    changed=sum(int(self._apply_quote(s,q)) for s,q in quotes.items())
                    if changed:self.version+=1; self.updated_at=time.time()
                    realtime=sum(1 for s in self.ready_symbols if self.rows[s].get('market_status')=='REALTIME'); delayed=sum(1 for s in self.ready_symbols if self.rows[s].get('market_status') in {'DELAYED','FROZEN_DELAYED'})
                    rt_non=sum(1 for s in self.ready_symbols if self.rows[s].get('market_status')=='REALTIME' and not self.rows[s].get('market_data_consolidated'))
                    self.connection_state='LIVE' if realtime else ('CONNECTED' if self.ready_symbols else 'WAITING')
                    session = self._ibkr.session_route()[0] if self._ibkr else 'UNKNOWN'; session_rt=sum(1 for s in self.ready_symbols if self.rows[s].get('market_session')==session and self.rows[s].get('market_status')=='REALTIME'); self.connection_message=f'IBKR {session}: {realtime} realtime ({session_rt} current-session), {delayed} delayed · scanned {len(self.scanned_symbols)}/{len(self.rows)} · cycle {self.scan_cycle}'
                    self.last_error=next(iter(errors.values()),None) if errors else None; backoff=2.0
                    await asyncio.sleep(max(.05,self._ibkr.poll_ms/1000-.1))
                except Exception as e:
                    self.connection_state='OFFLINE'; self.connection_message='IBKR connection unavailable'; self.last_error=str(e); await asyncio.sleep(backoff); backoff=min(20,backoff*1.5)
                    try: await self._ibkr.ensure_session()
                    except Exception: pass
        finally:
            if self._history_task: self._history_task.cancel()
            await self._ibkr.close()
