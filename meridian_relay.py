#!/usr/bin/env python3
"""Meridian local mover relay.

Runs on one ordinary laptop/network, scans TradingView's bulk US/Europe equity
scanner, and securely pushes only the resulting mover rows to the Render-hosted
Meridian instance. No Meridian server or IBKR Gateway is required locally.
"""
from __future__ import annotations
import argparse, json, math, os, sys, time
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo

TV_BASE='https://scanner.tradingview.com/{market}/scan'
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/129 Safari/537.36'
COLS=['name','description','close','change','volume','relative_volume_10d_calc','market_cap_basic','currency','premarket_close','premarket_change','premarket_volume','postmarket_close','postmarket_change','postmarket_volume','type','typespecs']

def num(v):
    try:
        x=float(v)
        return None if math.isnan(x) or math.isinf(x) else x
    except Exception:return None

def us_session():
    now=datetime.now(ZoneInfo('America/New_York'))
    if now.weekday()>4:return 'CLOSED'
    m=now.hour*60+now.minute
    if 240<=m<570:return 'PREMARKET'
    if 570<=m<960:return 'REGULAR'
    if 960<=m<1200:return 'AFTERHOURS'
    return 'CLOSED'

def payload(market,limit=60):
    pre=market=='america' and us_session()=='PREMARKET'
    return {'markets':[market],'symbols':{'query':{'types':[]},'tickers':[]},'options':{'lang':'en'},'columns':COLS,'filter':[{'left':'is_primary','operation':'equal','right':True},{'left':'type','operation':'equal','right':'stock'},{'left':'market_cap_basic','operation':'greater','right':1_000_000_000},{'left':'close','operation':'greater','right':5}], 'sort':{'sortBy':'premarket_change' if pre else 'change','sortOrder':'asc','nullsFirst':False},'range':[0,max(100,min(250,limit*4))],'ignore_unknown_fields':False}

def post_json(url,obj,headers=None,timeout=20):
    data=json.dumps(obj,separators=(',',':')).encode()
    h={'User-Agent':UA,'Accept':'application/json,text/plain,*/*','Content-Type':'application/json','Origin':'https://www.tradingview.com','Referer':'https://www.tradingview.com/'}
    if headers:h.update(headers)
    req=Request(url,data=data,headers=h,method='POST')
    with urlopen(req,timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))

def scan(market,region,limit=60):
    pl=payload(market,limit); body=post_json(TV_BASE.format(market=market),pl); idx={c:i for i,c in enumerate(COLS)}; out=[]
    pre=market=='america' and us_session()=='PREMARKET'; aft=market=='america' and us_session()=='AFTERHOURS'
    for raw in body.get('data') or []:
        ref=str(raw.get('s') or ''); vals=raw.get('d') or []
        if not ref or not vals:continue
        def v(k):
            i=idx[k]; return vals[i] if i<len(vals) else None
        ticker=str(v('name') or ref.split(':')[-1]).strip(); name=str(v('description') or ticker).strip(); close=num(v('close')); reg=num(v('change')); pc=num(v('premarket_close')); pchg=num(v('premarket_change')); ac=num(v('postmarket_close')); achg=num(v('postmarket_change'))
        if pre and pc is not None and pchg is not None:
            session='PREMARKET'; move=pchg; reference=pc/(1+pchg/100) if abs(1+pchg/100)>1e-9 else close; ext=pc; vol=num(v('premarket_volume'))
        elif aft and ac is not None and achg is not None:
            session='AFTERHOURS'; move=achg; reference=close; ext=ac; vol=num(v('postmarket_volume'))
        else:
            session='REGULAR' if market!='america' or us_session()=='REGULAR' else 'PREVIOUS_CLOSE'; move=reg; reference=close; ext=None; vol=num(v('volume'))
        if move is None or move>-1.5:continue
        ex=ref.split(':',1)[0] if ':' in ref else ''
        out.append({'symbol':ticker,'name':name,'region':region,'exchange':ex,'move':round(move,3),'price':reference,'extended_price':ext,'prior_close':reference if session=='PREMARKET' else None,'session':session,'market_state':session,'delay_minutes':None,'quote_source':'Local TradingView relay','currency':str(v('currency') or ''),'market_cap':num(v('market_cap_basic')),'volume':vol,'relative_volume':num(v('relative_volume_10d_calc')),'tv_symbol':ref})
    return sorted(out,key=lambda x:x['move'])[:limit]

def collect():
    rows=[]; errors=[]
    for market,region in [('america','US'),('europe','Europe')]:
        try:rows.extend(scan(market,region))
        except Exception as e:errors.append(f'{region}: {e}')
    seen=set(); merged=[]
    for x in sorted(rows,key=lambda x:x['move']):
        k=x.get('tv_symbol') or f"{x['region']}:{x['symbol']}"
        if k in seen:continue
        seen.add(k);merged.append(x)
        if len(merged)>=100:break
    return merged,errors

def push(base,token,rows):
    url=base.rstrip('/')+'/api/relay/movers'
    return post_json(url,{'rows':rows,'source':'work-laptop-tradingview','collected_at':time.time()},{'X-Meridian-Relay-Token':token,'Origin':base.rstrip('/'),'Referer':base.rstrip('/')+'/'})

def main():
    ap=argparse.ArgumentParser(description='Push local mover scans to cloud Meridian')
    ap.add_argument('--url',default=os.getenv('MERIDIAN_URL',''))
    ap.add_argument('--token',default=os.getenv('MERIDIAN_RELAY_TOKEN',''))
    ap.add_argument('--interval',type=int,default=90)
    ap.add_argument('--once',action='store_true')
    a=ap.parse_args()
    if not a.url or not a.token:
        print('Need --url and --token (or MERIDIAN_URL / MERIDIAN_RELAY_TOKEN).',file=sys.stderr);return 2
    print('Meridian relay started. Ctrl+C stops it.')
    while True:
        try:
            rows,errors=collect(); res=push(a.url,a.token,rows)
            print(datetime.now().strftime('%H:%M:%S'),f'uploaded {len(rows)} movers',(' | '+'; '.join(errors) if errors else ''),res)
        except (HTTPError,URLError,TimeoutError,Exception) as e:
            print(datetime.now().strftime('%H:%M:%S'),'relay error:',e,file=sys.stderr)
        if a.once:return 0
        time.sleep(max(45,a.interval))
if __name__=='__main__':raise SystemExit(main())
