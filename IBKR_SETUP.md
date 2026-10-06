# Meridian v2.4 — IBKR Market Data

Meridian can use Interactive Brokers Client Portal Gateway for genuine real-time/delayed top-of-book quotes. It does **not** fall back to demo prices when IBKR mode is enabled.

## 1. Start and authenticate Client Portal Gateway

Run the gateway on the same PC, open:

`https://localhost:5000`

Log in to IBKR and complete authentication. The local certificate warning is normal for the default gateway.

## 2. Configure `.env`

```env
MARKET_DATA_PROVIDER=ibkr
IBKR_GATEWAY_URL=https://localhost:5000/v1/api
IBKR_POLL_MS=500

NEWS_PROVIDER=free
SEC_USER_AGENT=Meridian your-real-email@example.com
```

Do not put your IBKR username or password in `.env`; brokerage authentication stays inside the local gateway/browser session.

## 3. Start Meridian

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --reload
```

Open `http://127.0.0.1:8000`.

## Free real-time vs delayed

IBKR includes complimentary non-consolidated real-time streaming data for US-listed stocks/ETFs, subject to account eligibility/entitlements. Meridian does not guess which feed it received. It reads IBKR market-data availability field `6509` and labels each quote accordingly:

- `REALTIME · NON-CONSOLIDATED`
- `REALTIME · CONSOLIDATED`
- `DELAYED`
- other IBKR status values when applicable

The Client Portal `/iserver/marketdata/snapshot` endpoint is used to read current values from IBKR's open top-of-book streams. It is not Meridian repeatedly purchasing the paid one-off regulatory snapshot product.

## Default universe and limits

v2.4 tracks **526 instruments** by default: **505 stocks** plus **21 Broad Market proxies**. It rotates them through a default 90-line window rather than trying to subscribe to everything simultaneously.

The Broad Market proxies are:

- 28 company stocks used by Live Dips / Movers / research.
- 21 liquid ETFs used only by the Broad Market tab.

That is below the Web API's 100-conid-per-snapshot-query limit and below the minimum 100 simultaneous market-data lines IBKR documents for clients. Startup history also stays within the 50 historical requests/minute pacing bucket.

For a future scanner covering hundreds or thousands of continuously streaming symbols, we should use a different architecture instead of trying to keep the entire universe live through one retail IBKR session.

## What is real in IBKR mode

- Current price and day move: IBKR.
- Quote data availability / consolidation status: IBKR field `6509`.
- Current volume: IBKR (note that non-consolidated real-time volume may not represent total consolidated US volume).
- 13-week high, 1-week change, volatility, average volume: IBKR historical daily bars.

Comparable-drop/recovery examples in the research drawer are still synthetic until the historical analogue engine is wired to the full historical data layer.
