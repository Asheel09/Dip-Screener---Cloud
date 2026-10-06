# Meridian changelog

- **v3.0 Cloud** — Yahoo-based global mover discovery, session-aware prices, cloud hosting and optional password protection. See `CHANGELOG_V3_0.md`.

# Meridian v2.8

See `CHANGELOG_V2_8.md` for the Mover Radar release.

# v2.1

- Live OpenAI web-search news in auto mode when an API key is configured.
- Richer catalyst cards with summaries, relevance, move connection and source links.
- Company descriptions in stock research drawer.
- Fixed stale stock drawer race when switching tickers quickly.
- Added forced Refresh research action.
- News cache defaults to 15 minutes.

# Changelog

## 2.4.0

- 505-stock screening universe plus 21 Broad Market proxies.
- Rotating IBKR market-data windows under the simultaneous-line limit.
- Complimentary IEX WebSocket feed attempted first; SMART snapshot fallback for symbols without a real-time IEX response.
- Progressive/cached 3-month history enrichment under IBKR historical pacing limits.
- Default visible dip-row limit raised to 250.


## 2.0.0
- Added persistent live runtime settings.
- Added AI Research tab and per-stock AI research box.
- Added safe natural-language Customize workflow.
- Added grouped modular navigation.
- Made WebSocket push interval dynamic.
- Added API status for AI integration.
- Expanded API tests to cover settings and AI-safe failure behavior.

## 1.0.0
- Initial live dashboard prototype with demo market stream, live dips, movers, news, comparable drops, watchlist and backtests.

## 2.3.0
- Added separate Broad Market tab and expanded the default IBKR universe to 49 instruments.
- Added free GDELT/SEC/Fed/BLS news ingestion and persistent event storage.
- Added IBKR real-time consolidated/non-consolidated quote diagnostics.

## v2.6.0
- Session-aware US equity pricing: OVERNIGHT (IBKR overnight venue), PREMARKET/AFTERHOURS (EDGX), REGULAR (IEX/free-stream path), with SMART fallback.
- Price rows now expose the active session so overnight/premarket prints are not confused with the prior close.
- Replaced placeholder/synthetic recovery scores with real 5-year IBKR history + SPY benchmark analogues.
- Stocks remain Pending until real recovery evidence is calculated; fewer than four comparable episodes are labelled Insufficient rather than assigned a fake 50.
- Company research suppresses stale routine SEC filing dumps and limits the free-source catalyst list to fresh, plausibly relevant items.

## v2.7.0
- Added three-state sortable screener headers: Default → Asc → Desc.
- Added optional column filters for price, daily/weekly move, drawdown, severity, recovery and reason text.
- Moved Recovery directly beside Severity; renamed Why moving to Reason and moved it to the far right.
- IBKR mode no longer inherits seeded/demo catalyst text; unsupported reasons are blank until current research verifies a catalyst.
- Generic Meridian filler company descriptions are suppressed rather than shown as research.
- Research drawer renders local metrics immediately, then loads latest company news asynchronously.
- Company news prioritizes recent GDELT web coverage; very recent SEC filings may appear as secondary context but never establish causality by themselves.
- News & Catalysts supports All / Macro / Specific stock views, keeps its client-side cache across tab switches, and shows current screener research leads with recent price changes.
- Added a five-minute server-side market-news cache.
- Extended IBKR historical feature cache from 6 hours to 7 days to avoid daily full-history rebuilds.
- WebSocket payload now computes the 526-instrument snapshot once per push instead of twice.


## v3.2
See `CHANGELOG_V3_2.md`.

## v3.3
- Relay-side catalyst/news enrichment for Mover Radar so Render does not need to rediscover reasons from blocked cloud IPs.
