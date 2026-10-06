# Meridian v3.0 Cloud

## Architecture
- Meridian can now run as a cloud-hosted FastAPI web service with `MARKET_DATA_PROVIDER=yahoo`.
- IBKR Client Portal Gateway is no longer required for the primary scanner.
- Default landing tab is **Mover Radar**.
- Added optional password protection using `MERIDIAN_PASSWORD`.
- Added browser WebSocket heartbeats so an actively-open dashboard sends inbound traffic to the host.

## Market discovery
- Mover Radar uses Yahoo/yfinance equity screening rather than a fixed IBKR universe.
- Europe scans France, Germany, UK, Netherlands, Switzerland and Italy.
- During US premarket, Meridian samples approximately the largest 500 US companies and ranks them by premarket move when Yahoo supplies premarket data.
- During regular sessions, Mover Radar ranks downside movers by normal-session percentage change.

## Session-aware prices
- Outside the US regular session, the main/core table keeps **Previous close** as the reference price.
- Premarket/after-hours prices are shown separately in Mover Radar when available.
- European rows expose Yahoo's exchange delay in minutes when supplied.
- Closed-market prices are labeled as references rather than presented as live trades.

## Cloud performance
- Only the compact priority + broad-market core list is refreshed continuously.
- Global discovery happens through Yahoo screener queries instead of refreshing 500+ hard-coded symbols.
- Core market reference data refreshes every 5 minutes by default.
- Global mover scans cache for 2 minutes.
- Recovery history is loaded on demand and cached in-process.

## Hosting
- `render.yaml` is configured for Render's Free web-service plan.
- The Docker image binds to Render's supplied `PORT`.
- Free-host filesystem persistence is not required for core scanner operation; local caches/settings can be rebuilt after a restart.
