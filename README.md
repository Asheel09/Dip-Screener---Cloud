# Meridian Market Dashboard v3.0 Cloud

Meridian v3.0 is the cloud-first version of the market-move scanner. Deploy it once, then open the same password-protected URL from a work laptop, personal laptop, tablet or phone. The primary scanner no longer requires IBKR Client Portal Gateway.

## What Meridian is optimized for

The first-stage question is **“What fell hard today, and why?”**

- **Mover Radar** discovers large downside movers across the US and major European markets.
- **Free news research** checks GDELT, SEC EDGAR and official macro feeds for a plausible catalyst.
- **Live Dips / Broad Market** keep a compact core set of priority names and market proxies available without trying to poll 500+ hard-coded symbols continuously.
- **Recovery research** loads real five-year price history on demand; synthetic analogue tables are not used.

## Workday price policy

Meridian deliberately does not pretend every number is a live regular-session trade.

- **US before 09:30 ET:** the main/reference price is the previous regular close. If Yahoo supplies premarket data, Mover Radar shows the premarket price and premarket % change separately.
- **US 09:30–16:00 ET:** use the latest regular-session Yahoo market data available.
- **US after hours:** the regular close remains the reference, with an extended-hours move shown separately when available.
- **Europe while open:** use Yahoo's latest exchange quote and show `exchangeDataDelayedBy` when supplied (many major European venues are delayed roughly 15–30 minutes).
- **Closed markets:** previous-close values are labeled as references, never as live trades.

## Cloud defaults

```env
MARKET_DATA_PROVIDER=yahoo
NEWS_PROVIDER=free
YAHOO_CORE_REFRESH_SECONDS=300
SEC_USER_AGENT=Meridian your-real-email@example.com
MERIDIAN_PASSWORD=choose-a-password
```

`OPENAI_API_KEY` remains optional. It is not needed for Mover Radar, prices, recovery history or the free news pipeline.

## Local run (optional)

```bash
python -m venv .venv
# Windows PowerShell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app:app --host 0.0.0.0 --port 8000
```

Open `http://127.0.0.1:8000`.

## Cloud deployment

See **`CLOUD_DEPLOY.md`**. The repository includes:

- `Dockerfile`
- `render.yaml` configured for a Render Free web service
- `/api/health` health check
- optional `MERIDIAN_PASSWORD` cookie login
- WebSocket client heartbeat for an actively-open dashboard

## Free data sources

### Market discovery / prices
Yahoo Finance via `yfinance`.

### News / catalysts
- GDELT DOC API
- SEC EDGAR
- Federal Reserve feeds
- BLS feeds

## Persistence on a free host

Render Free has an ephemeral filesystem. Meridian therefore treats local history/news files as rebuildable caches. A restart or redeploy can reset runtime settings and caches, but the scanner itself continues to work from public market/news sources.

Your Git repository becomes the master copy for future Meridian updates.

## When can I delete the laptop copy?

Only after:

1. Render says the deployment is **Live**.
2. `/api/health` reports version `3.3.0`.
3. Mover Radar returns real Yahoo US/European results.
4. The same URL works from a second device.

Once those four checks pass, the local project folder is not required to run Meridian. Keep the cloud source repository.

## Legacy IBKR mode

The v2.x IBKR code remains in the project for fallback/testing. To use it locally, set `MARKET_DATA_PROVIDER=ibkr` and run an authenticated Client Portal Gateway. It is no longer the recommended cloud scanner architecture.

## Tests

```bash
python -m unittest discover -s tests -v
```


## Local mover relay (free cloud workaround)

Render shared IPs can be rate-limited by anonymous Yahoo/TradingView endpoints. Meridian v3.2 can therefore accept mover snapshots from one lightweight local collector while the dashboard itself remains hosted on Render.

1. Add `MERIDIAN_RELAY_TOKEN` in Render Environment. Use a long random value.
2. Download only `meridian_relay.py` onto the work laptop.
3. Run:

```powershell
python meridian_relay.py --url https://YOUR-SERVICE.onrender.com --token YOUR_RELAY_TOKEN
```

The collector scans about every 90 seconds and uploads only mover rows. Phones and other computers still need only the Render URL. Stop it with Ctrl+C. If the relay stops, Meridian keeps the last snapshot and labels it stale after 10 minutes.
