"""Walk-forward validation for the exact live comparable-dislocation model.

Example:
  python validate_model.py --universe universe.csv --limit 100 --output validation

This is intentionally manual, not part of the weekly production workflow. It reuses
`comparable_dislocation_candidate`, so validation and production cannot silently drift.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path

import pandas as pd

from dislocation_model import comparable_dislocation_candidate, RECOVERY_FRACTION, MAX_RECOVERY_WEEKS
from screener import read_universe, benchmark_return, write_csv
from wide_scan import download_weekly

COLUMNS = ["ticker","signal_date","grade","reliability_score","entry_price","current_drawdown","sample_size",
           "historical_recovery_rate","historical_positive_12w","return_12w","return_20w","excess_12w",
           "recovered_75pct","recovery_weeks"]


def median(xs):
    return statistics.median(xs) if xs else None


def validate_symbol(ticker: str, p: pd.Series, b: pd.Series, start_year: int = 2018, step_weeks: int = 4) -> list[dict]:
    rows=[]; last_signal=-99
    for i in range(180, len(p)-MAX_RECOVERY_WEEKS-1, step_weeks):
        if p.index[i].year < start_year or i-last_signal < 12:
            continue
        train=p.iloc[:i+1]
        btrain=b.loc[b.index <= p.index[i]]
        status,stats,_=comparable_dislocation_candidate(train,btrain)
        if status != 'PASS':
            continue
        entry=float(p.iloc[i]); peak=float(stats.get('current_peak_price') or entry)
        target=entry+RECOVERY_FRACTION*(peak-entry)
        future=p.iloc[i+1:i+1+MAX_RECOVERY_WEEKS]
        reached=[j for j,v in enumerate(future,start=1) if float(v)>=target]
        rw=reached[0] if reached else None
        r12=float(p.iloc[i+12]/entry-1) if i+12<len(p) else None
        r20=float(p.iloc[i+20]/entry-1) if i+20<len(p) else None
        bench=benchmark_return(b,p.index[i],p.index[i+12]) if i+12<len(p) else None
        rows.append({"ticker":ticker,"signal_date":p.index[i].date().isoformat(),"grade":stats.get('signal_grade'),
                     "reliability_score":stats.get('reliability_score'),"entry_price":entry,
                     "current_drawdown":stats.get('current_drawdown'),"sample_size":stats.get('sample_size'),
                     "historical_recovery_rate":stats.get('recovery_rate'),"historical_positive_12w":stats.get('positive_12w_rate'),
                     "return_12w":r12,"return_20w":r20,"excess_12w":(r12-bench if r12 is not None and bench is not None else None),
                     "recovered_75pct":rw is not None,"recovery_weeks":rw})
        last_signal=i
    return rows


def render(rows: list[dict], attempted: int) -> str:
    r12=[float(x['return_12w']) for x in rows if x.get('return_12w') not in (None,'')]
    ex=[float(x['excess_12w']) for x in rows if x.get('excess_12w') not in (None,'')]
    rec=[x for x in rows if x.get('recovered_75pct')]
    positive=sum(x>0 for x in r12)
    beat=sum(x>0 for x in ex)
    lines=["# Walk-forward model validation","",
           f"**Stocks attempted:** {attempted} · **Non-overlapping PASS signals:** {len(rows)}","",
           "> Every historical signal is generated from a price series truncated at that date. The future 12/20-week results are measured only after the live model has made its historical decision.",""]
    if rows:
        lines += [f"- Positive 12-week outcomes: **{positive/len(r12):.1%}** ({positive}/{len(r12)})" if r12 else "- Positive 12-week outcomes: —",
                  f"- 75% drawdown recovery within 20 weeks: **{len(rec)/len(rows):.1%}** ({len(rec)}/{len(rows)})",
                  f"- Median 12-week return: **{median(r12):+.1%}**" if r12 else "- Median 12-week return: —",
                  f"- Median 12-week excess vs SPY: **{median(ex):+.1%}**" if ex else "- Median excess: —",
                  f"- Beat SPY at 12 weeks: **{beat/len(ex):.1%}** ({beat}/{len(ex)})" if ex else "- Beat SPY: —",
                  f"- Worst 12-week result: **{min(r12):+.1%}**" if r12 else "- Worst 12-week result: —",""]
    lines += ["| Ticker | Signal date | Grade | Reliability then | Drawdown | Prior samples | Historical recovery | Actual 12w | Actual 20w | Actual vs SPY | 75% recovered? |",
              "|---|---|:---:|---:|---:|---:|---:|---:|---:|---:|:---:|"]
    for x in rows:
        f=lambda v: '—' if v in (None,'') else f"{float(v):+.1%}"
        lines.append(f"| {x['ticker']} | {x['signal_date']} | {x.get('grade','—')} | {float(x.get('reliability_score') or 0):.0f} | {f(x.get('current_drawdown'))} | {x.get('sample_size','—')} | {f(x.get('historical_recovery_rate'))} | {f(x.get('return_12w'))} | {f(x.get('return_20w'))} | {f(x.get('excess_12w'))} | {'YES' if x.get('recovered_75pct') else 'NO'} |")
    return '\n'.join(lines)+'\n'


def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('--universe',type=Path,default=Path('universe.csv')); ap.add_argument('--limit',type=int,default=100)
    ap.add_argument('--start-year',type=int,default=2018); ap.add_argument('--step-weeks',type=int,default=4); ap.add_argument('--output',type=Path,default=Path('validation'))
    a=ap.parse_args(argv)
    universe=read_universe(a.universe,a.limit); symbols=[x['ticker'] for x in universe]
    data,errors=download_weekly(['SPY']+symbols)
    if 'SPY' not in data: raise SystemExit('SPY benchmark unavailable: '+errors.get('SPY',''))
    rows=[]
    for n,s in enumerate(symbols,1):
        if s not in data: continue
        rows.extend(validate_symbol(s,data[s],data['SPY'],a.start_year,a.step_weeks))
        if n%10==0: print(f'validated {n}/{len(symbols)} stocks, {len(rows)} signals',flush=True)
    a.output.mkdir(parents=True,exist_ok=True); write_csv(a.output/'signals.csv',rows,COLUMNS)
    (a.output/'report.md').write_text(render(rows,len(symbols)),encoding='utf-8')
    (a.output/'meta.json').write_text(json.dumps({'stocks_attempted':len(symbols),'signals':len(rows),'price_errors':errors},indent=2),encoding='utf-8')
    print(f'Wrote {len(rows)} walk-forward signals to {a.output}')
    return 0

if __name__=='__main__': raise SystemExit(main())
