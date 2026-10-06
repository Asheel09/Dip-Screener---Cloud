"""Personal research screener. Not investment advice; historical signals are exploratory."""
from __future__ import annotations

import argparse
import csv
import html
import json
import math
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import pandas as pd

WEEKLY_MIN = 130
HISTORY_YEARS = 15
METRICS = ["ticker", "company", "sector", "strategy", "status", "signal", "sample_size", "win_rate", "median_forward_return", "mean_forward_return", "median_excess_return", "worst_forward_return", "train_median", "holdout_median", "latest_price", "latest_date", "detail", "source_url"]
SCAN_COLUMNS = ["ticker", "company", "sector", "latest_date", "weekly_rows", "seasonality", "mean_reversion", "momentum", "event_driven", "error"]


@dataclass
class Finding:
    ticker: str
    company: str
    sector: str
    strategy: str
    status: str
    signal: str
    sample_size: int = 0
    win_rate: float | None = None
    median_forward_return: float | None = None
    mean_forward_return: float | None = None
    median_excess_return: float | None = None
    worst_forward_return: float | None = None
    train_median: float | None = None
    holdout_median: float | None = None
    latest_price: float | None = None
    latest_date: str = ""
    detail: str = ""
    source_url: str = ""


def returns_summary(samples: list[tuple[float, float]]) -> dict:
    """Each observation is an independent historical entry's stock and benchmark return."""
    if not samples:
        return {}
    import statistics
    outcomes = [float(a) for a, _ in samples]
    excess = [float(a - b) for a, b in samples]
    cut = max(1, int(len(outcomes) * .7))
    holdout = outcomes[cut:]
    return {"sample_size": len(outcomes), "win_rate": sum(x > 0 for x in outcomes) / len(outcomes),
            "median_forward_return": statistics.median(outcomes), "mean_forward_return": statistics.mean(outcomes),
            "median_excess_return": statistics.median(excess), "worst_forward_return": min(outcomes),
            "train_median": statistics.median(outcomes[:cut]),
            "holdout_median": statistics.median(holdout) if holdout else None}


def clean_prices(series: pd.Series) -> pd.Series:
    series = pd.to_numeric(series, errors="coerce").dropna().sort_index()
    series = series[~series.index.duplicated(keep="last")]
    series = series[series > 0]
    if getattr(series.index, "tz", None) is not None:
        series.index = series.index.tz_localize(None)
    return series


def get_return(prices: pd.Series, start: int, horizon: int) -> float:
    return float(prices.iloc[start + horizon] / prices.iloc[start] - 1)


def benchmark_return(benchmark: pd.Series, start_date: pd.Timestamp, end_date: pd.Timestamp) -> float | None:
    # Exact weekly dates only: no joining to an unrelated date that biases the comparison.
    if start_date not in benchmark.index or end_date not in benchmark.index:
        return None
    return float(benchmark.loc[end_date] / benchmark.loc[start_date] - 1)


def seasonal(prices: pd.Series, benchmark: pd.Series, as_of: datetime, horizon: int = 8) -> tuple[bool, dict, str]:
    """Current ISO calendar week, one non-overlapping observation from each *completed* past year."""
    if len(prices) < WEEKLY_MIN:
        return False, {}, "Insufficient history"
    week = prices.index[-1].isocalendar().week
    years = {}
    for i, d in enumerate(prices.index):
        if d.year >= as_of.year or d.isocalendar().week != week or i + horizon >= len(prices):
            continue
        # Only use completed exits, avoid multiple qualifying entries in the same year.
        if prices.index[i + horizon].year >= as_of.year or d.year in years:
            continue
        b = benchmark_return(benchmark, d, prices.index[i + horizon])
        if b is None:
            continue
        years[d.year] = (get_return(prices, i, horizon), b)
    samples = [years[y] for y in sorted(years)]
    stats = returns_summary(samples)
    passed = (len(samples) >= 8 and stats["win_rate"] >= .625 and
              stats["median_forward_return"] >= .025 and stats["median_excess_return"] > 0)
    return passed, stats, f"ISO week {week}; {horizon}-week hold; one trade per completed year. Historical pattern only."


def nonoverlap_signals(prices: pd.Series, benchmark: pd.Series, condition, horizon: int = 8,
                       warmup: int = 40) -> list[tuple[float, float]]:
    samples = []
    i = warmup
    while i + horizon < len(prices) - 1:  # do not use a still-unfinished recent horizon
        if condition(prices, i):
            b = benchmark_return(benchmark, prices.index[i], prices.index[i + horizon])
            if b is not None:
                samples.append((get_return(prices, i, horizon), b))
                i += horizon  # prevent overlapping outcomes masquerading as independent
                continue
        i += 1
    return samples


def reversion(prices: pd.Series, benchmark: pd.Series) -> tuple[bool, dict, str]:
    if len(prices) < WEEKLY_MIN:
        return False, {}, "Insufficient history"
    fall13 = float(prices.iloc[-1] / prices.iloc[-14] - 1)
    cond = lambda p, i: float(p.iloc[i] / p.iloc[i - 13] - 1) <= -.15
    samples = nonoverlap_signals(prices, benchmark, cond)
    stats = returns_summary(samples)
    passed = (fall13 <= -.15 and len(samples) >= 5 and stats["win_rate"] >= .60 and
              stats["median_forward_return"] > .025 and stats["median_excess_return"] > 0)
    return passed, stats, f"13-week change {fall13:+.1%}; historical trigger <= -15%; 8-week non-overlapping forward returns."


def momentum(prices: pd.Series, benchmark: pd.Series) -> tuple[bool, dict, str]:
    if len(prices) < WEEKLY_MIN:
        return False, {}, "Insufficient history"
    ret26 = float(prices.iloc[-1] / prices.iloc[-27] - 1)
    ret13 = float(prices.iloc[-1] / prices.iloc[-14] - 1)
    sp26 = benchmark_return(benchmark, prices.index[-27], prices.index[-1])
    above_avg = float(prices.iloc[-1] / prices.iloc[-40:].mean() - 1)
    passed = (sp26 is not None and ret26 >= .10 and ret13 > 0 and above_avg > 0 and
              ret26 - sp26 >= .05)
    return passed, {}, (f"26-week {ret26:+.1%}; 13-week {ret13:+.1%}; vs 40-week average {above_avg:+.1%}; "
                        f"26-week excess vs SPY {ret26-sp26:+.1%}. Trend signal, NOT a validated forward-return backtest."
                        if sp26 is not None else "SPY history does not align; momentum unavailable.")


def parse_events(path: Path) -> dict[str, list[dict]]:
    if not path.exists():
        return {}
    result = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            ticker = row.get("ticker", "").strip().upper()
            date = row.get("date", "").strip()
            source = row.get("source_url", "").strip()
            if not ticker or not date or not source.startswith(("https://", "http://")):
                continue
            try:
                pd.Timestamp(date)
            except ValueError:
                continue
            result.setdefault(ticker, []).append({"date": date, "event_type": row.get("event_type", "earnings").strip(), "source_url": source})
    return result


def event_signal(daily: pd.Series, benchmark: pd.Series, events: list[dict],
                 as_of: datetime) -> tuple[bool, dict, str]:
    """Only handles sourced events provided in events.csv; enters after next trading day CLOSE.

    A weekly close cannot establish executable returns around an earnings announcement.
    """
    today = pd.Timestamp(as_of.date())
    upcoming = [e for e in events if -7 <= (pd.Timestamp(e["date"]) - today).days <= 14]
    if not upcoming:
        return False, {}, "No sourced recent/upcoming event in [-7,+14] days"
    chosen = sorted(upcoming, key=lambda e: abs((pd.Timestamp(e["date"]) - today).days))[0]
    kind = chosen["event_type"]
    hist = sorted([e for e in events if e["event_type"] == kind and
                   pd.Timestamp(e["date"]) < today - pd.Timedelta(days=45)], key=lambda x: x["date"])
    samples = []
    last_exit = pd.Timestamp("1900-01-01")
    for e in hist:
        d = pd.Timestamp(e["date"])
        # Entry is first daily close strictly AFTER event date (time of release may be unknown).
        i = int(daily.index.searchsorted(d, side="right"))
        if i + 20 >= len(daily) or i <= 0 or daily.index[i] <= last_exit:
            continue
        b = benchmark_return(benchmark, daily.index[i], daily.index[i + 20])
        if b is None:
            continue
        samples.append((get_return(daily, i, 20), b))
        last_exit = daily.index[i + 20]
    stats = returns_summary(samples)
    passed = (len(samples) >= 5 and stats["win_rate"] >= .60 and
              stats["median_forward_return"] >= .02 and stats["median_excess_return"] > 0)
    return passed, stats, (f"{kind} dated {chosen['date']}; 20 trading-day historical returns from next day's close; "
                           f"{len(samples)} sourced, nonoverlapping examples. Event dates are supplied, NOT auto-discovered.")


def read_universe(path: Path, maximum: int) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    seen = set()
    cleaned = []
    for row in rows:
        ticker = row.get("ticker", "").strip().upper()
        if not ticker or ticker == "SPY" or ticker in seen or not all(ch.isalnum() or ch in '.-' for ch in ticker):
            continue
        seen.add(ticker)
        cleaned.append({"ticker": ticker, "company": row.get("company", ticker).strip() or ticker,
                        "sector": row.get("sector", "Unspecified").strip() or "Unspecified",
                        "exchange": row.get("exchange", "").strip()})
        if len(cleaned) >= maximum:
            break
    return cleaned


def fetch_yahoo(ticker: str, interval: str) -> pd.Series:
    import yfinance as yf  # imported only when live data is requested
    hist = yf.Ticker(ticker).history(period="max", interval=interval, auto_adjust=True, actions=False,
                                    raise_errors=True, timeout=25)
    if hist is None or hist.empty or "Close" not in hist:
        raise ValueError(f"No {interval} data returned for {ticker}")
    prices = clean_prices(hist["Close"])
    cutoff = pd.Timestamp(datetime.now(timezone.utc).date() - timedelta(days=HISTORY_YEARS * 366))
    return prices.loc[prices.index >= cutoff]


def fetch_with_retry(ticker: str, interval: str, attempts: int = 2) -> pd.Series:
    for n in range(attempts):
        try:
            return fetch_yahoo(ticker, interval)
        except Exception:
            if n == attempts - 1:
                raise
            time.sleep(2 * (n + 1))
    raise RuntimeError("unreachable")


def write_csv(path: Path, rows: list[dict], columns: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            # Defend against CSV formula injection if a downloaded field ever starts with a formula character.
            safe = {k: ("'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v)
                    for k, v in row.items()}
            writer.writerow(safe)


def fmt_pct(value):
    return "—" if value is None or (isinstance(value, float) and math.isnan(value)) else f"{value:+.1%}"


def render_report(timestamp: str, universe: list[dict], coverage: list[dict], findings: list[dict],
                  event_count: int, benchmark_date: str) -> str:
    failures = [r for r in coverage if r["error"]]
    passes = [r for r in findings if r["status"] == "PASS"]
    lines = ["# Weekly Pattern Discovery", "", f"**Run:** {timestamp} UTC · **Benchmark:** SPY, latest weekly point {benchmark_date}",
             f"**Coverage:** {len(coverage)}/{len(universe)} processed; {len(failures)} price-data failures; {len(passes)} strategy matches.", "",
             "> Exploratory historical signals, **not buy recommendations**. A current positive signal is not proof of future returns. "
             "The universe is a fixed current list (survivorship bias); results exclude transaction costs, taxes and spread. "
             "Historical seasonal samples use one observation per completed year. Data is Yahoo via unofficial yfinance "
             "for personal research; check licensing/availability.", "",
             "## Screening matches", ""]
    if not passes:
        lines += ["No candidates passed the conservative starter criteria. This is a valid result; see coverage and all signals below.", ""]
    else:
        lines += ["| Stock | Strategy | Historical sample | Win rate | Median forward | Median excess vs SPY | Worst forward | Note |",
                  "|---|---|---:|---:|---:|---:|---:|---|"]
        for f in passes:
            desc = f["detail"].replace("|", "/")
            lines.append(f"| [{f['ticker']}]({f['source_url']}) | {f['strategy']} | {f['sample_size'] or '—'} | "
                         f"{fmt_pct(f['win_rate'])} | {fmt_pct(f['median_forward_return'])} | "
                         f"{fmt_pct(f['median_excess_return'])} | {fmt_pct(f['worst_forward_return'])} | {desc} |")
        lines.append("")
    lines += ["## Everything scanned — passes and fails", "",
              "Use `scanned.csv` to see each strategy's verdict for **every** ticker (no early stop at first failed strategy).", "",
              "| Ticker | Seasonality | Mean reversion | Momentum | Event-driven | Price data |",
              "|---|---|---|---|---|---|"]
    for r in coverage:
        lines.append(f"| {r['ticker']} | {r['seasonality']} | {r['mean_reversion']} | {r['momentum']} | "
                     f"{r['event_driven']} | {'ERROR: ' + str(r['error']).replace('|','/')[:90] if r['error'] else r['latest_date']} |")
    lines += ["", "## Data and interpretation", "",
              "- **Seasonality:** same ISO week of past completed calendar years, followed for eight weeks; minimum eight independent years. Results are exploratory: the same universe and rule were chosen by a researcher, not pre-registered.",
              "- **Mean reversion:** 13-week fall of at least 15%; historical instances sampled without overlapping eight-week outcomes.",
              "- **Momentum:** 26-week strength plus three trend tests; **no forward-return statistics are claimed** for this strategy.",
              f"- **Event-driven:** {event_count} tickers have valid events in `events.csv`. A dated, sourced event list is mandatory; the tool does not invent or automatically discover earnings dates. It uses daily adjusted closes for affected tickers only, entries after the next trading-day close, and 20-day historical holding windows. Missing events = unavailable, not a negative signal.",
              "- **Backtest caution:** small samples, data-provider revisions, survivorship and multiple-testing bias can produce spurious patterns. Train/holdout statistics in `findings.csv` are *descriptive*, not independent validation of a strategy optimized on the same universe.",
              "- **Sources:** ticker historical pricing pages are linked in `findings.csv`; supplied corporate event dates have source URLs in `events.csv`. Historical Yahoo availability and corporate-action adjustments should be checked independently.", "",
              "## Files", "", "`findings.csv`: every computed stock/strategy status and available statistics.  ",
              "`scanned.csv`: complete stock coverage, including errors.  ",
              "`latest.md`: this readable report. Each run also gets a dated copy in `reports/history/`.", ""]
    if failures:
        lines += ["## Provider failures", "", "Do not interpret missing data as a failed investment signal.", ""]
        for r in failures:
            lines.append(f"- {r['ticker']}: {str(r['error'])[:170]}")
    return "\n".join(lines) + "\n"


def execute(universe: list[dict], weekly_source, daily_source, events: dict, out: Path,
            as_of: datetime) -> tuple[list[dict], list[dict]]:
    benchmark = clean_prices(weekly_source("SPY"))
    if len(benchmark) < WEEKLY_MIN:
        raise RuntimeError("SPY benchmark unavailable or too short. Refusing to generate misleading results.")
    event_benchmark = None
    results = []
    coverage = []
    for ix, stock in enumerate(universe, start=1):
        symbol = stock["ticker"]
        print(f"[{ix}/{len(universe)}] {symbol}: fetching and checking every strategy", flush=True)
        record = dict(ticker=symbol, company=stock["company"], sector=stock["sector"], latest_date="", weekly_rows=0,
                      seasonality="UNAVAILABLE", mean_reversion="UNAVAILABLE", momentum="UNAVAILABLE",
                      event_driven="UNAVAILABLE", error="")
        try:
            prices = clean_prices(weekly_source(symbol))
            if prices.empty:
                raise ValueError("No usable positive adjusted closes")
            if len(prices) < WEEKLY_MIN:
                raise ValueError(f"Only {len(prices)} weekly rows (< {WEEKLY_MIN} minimum)")
            record["latest_date"] = str(prices.index[-1].date())
            record["weekly_rows"] = len(prices)
            if (pd.Timestamp(as_of.date()) - prices.index[-1]).days > 21:
                raise ValueError("Last reported close is >21 days stale")
            url = "https://finance.yahoo.com/quote/" + quote(symbol, safe="") + "/history/"
            for key, label, func in [("seasonality", "Seasonality", lambda: seasonal(prices, benchmark, as_of)),
                                     ("mean_reversion", "Mean reversion", lambda: reversion(prices, benchmark)),
                                     ("momentum", "Momentum", lambda: momentum(prices, benchmark))]:
                try:
                    passed, stats, detail = func()
                    verdict = "PASS" if passed else "FAIL"
                    if "unavailable" in detail.lower():
                        verdict = "UNAVAILABLE"
                    record[key] = verdict
                    results.append(asdict(Finding(symbol, stock["company"], stock["sector"], label, verdict,
                                                  "Candidate" if passed else "No match", latest_price=float(prices.iloc[-1]),
                                                  latest_date=str(prices.index[-1].date()), detail=detail,
                                                  source_url=url, **stats)))
                except Exception as e:
                    record[key] = "ERROR"
                    results.append(asdict(Finding(symbol, stock["company"], stock["sector"], label, "ERROR", "Not evaluated",
                                                  detail=type(e).__name__ + ": " + str(e)[:150], source_url=url)))
            if symbol in events:
                try:
                    if event_benchmark is None:
                        event_benchmark = clean_prices(daily_source("SPY"))
                    dprices = clean_prices(daily_source(symbol))
                    passed, stats, detail = event_signal(dprices, event_benchmark, events[symbol], as_of)
                    record["event_driven"] = "PASS" if passed else "FAIL"
                    results.append(asdict(Finding(symbol, stock["company"], stock["sector"], "Event-driven",
                                                  record["event_driven"], "Candidate" if passed else "No match",
                                                  latest_price=float(prices.iloc[-1]), latest_date=record["latest_date"],
                                                  detail=detail, source_url=events[symbol][0]["source_url"], **stats)))
                except Exception as e:
                    record["event_driven"] = "ERROR"
                    results.append(asdict(Finding(symbol, stock["company"], stock["sector"], "Event-driven", "ERROR",
                                                  "Not evaluated", detail=type(e).__name__ + ": " + str(e)[:150],
                                                  source_url=url)))
        except Exception as e:
            record["error"] = type(e).__name__ + ": " + str(e)[:180]
            print(f"  WARN {record['error']} — continuing to next stock", flush=True)
        coverage.append(record)
        time.sleep(.20)
    timestamp = as_of.strftime("%Y-%m-%d %H:%M")
    folder = out / "history" / as_of.strftime("%Y-%m-%d_%H%M%S")
    folder.mkdir(parents=True, exist_ok=True)
    write_csv(folder / "findings.csv", results, METRICS)
    write_csv(folder / "scanned.csv", coverage, SCAN_COLUMNS)
    md = render_report(timestamp, universe, coverage, results, len(events), str(benchmark.index[-1].date()))
    (folder / "report.md").write_text(md, encoding="utf-8")
    (out / "latest.md").write_text(md, encoding="utf-8")
    for file in ("findings.csv", "scanned.csv"):
        shutil.copyfile(folder / file, out / file)
    (out / "latest.json").write_text(json.dumps({"run_utc": timestamp, "universe_size": len(universe),
                               "price_failures": sum(bool(x["error"]) for x in coverage),
                               "candidates": sum(x["status"] == "PASS" for x in results)}, indent=2), encoding="utf-8")
    return coverage, results


def main(argv=None):
    p = argparse.ArgumentParser(description="Weekly discovery scanner: no AI API or payment required; personal research.")
    p.add_argument("--universe", type=Path, default=Path("universe.csv"))
    p.add_argument("--events", type=Path, default=Path("events.csv"))
    p.add_argument("--output", type=Path, default=Path("reports"))
    p.add_argument("--max-stocks", type=int, default=50)
    args = p.parse_args(argv)
    if not 1 <= args.max_stocks <= 200:
        p.error("--max-stocks must be between 1 and 200; larger universes need a properly licensed data feed.")
    universe = read_universe(args.universe, args.max_stocks)
    if not universe:
        p.error("No valid stocks in universe.csv")
    events = parse_events(args.events)
    now = datetime.now(timezone.utc)
    try:
        coverage, results = execute(universe, lambda t: fetch_with_retry(t, "1wk"),
                                    lambda t: fetch_with_retry(t, "1d"), events, args.output, now)
    except Exception as e:
        print("FATAL: benchmark unavailable or cannot generate report: " + str(e), file=sys.stderr)
        return 2
    print(f"FINISHED {len(coverage)} stocks, {sum(r['status']=='PASS' for r in results)} matches, "
          f"{sum(bool(r['error']) for r in coverage)} price failures. Report: {args.output/'latest.md'}", flush=True)
    return 0 if any(not r["error"] for r in coverage) else 2


if __name__ == "__main__":
    raise SystemExit(main())
