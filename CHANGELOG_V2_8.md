# Meridian v2.8

## Mover Radar

- Replaces the old local-universe Movers view with IBKR's global Market Scanner.
- Runs **Top % Losers** against US major exchanges and major European primary venues.
- European discovery dynamically reads IBKR scanner parameters and prefers Paris/SBF, Xetra/IBIS, London/LSE, Amsterdam/AEB, Switzerland/EBS and Milan/BVME when available.
- Scanner parameter metadata is cached for 15 minutes, matching IBKR's endpoint pacing limit.
- Scanner results are cached for 30 seconds and auto-refresh approximately once per minute while Mover Radar is open.
- Uses price/market-cap scanner filters when the current IBKR entitlements permit them, with an entitlement-safe unfiltered fallback.
- ETF/leveraged-fund results are heuristically removed so the page is biased toward operating companies.
- The top interesting losers receive asynchronous company-news checks without blocking the scanner result.

## Catalyst classification

- Added analyst-action classification (upgrade, downgrade, price target, rating).
- Added supply/demand classification (capacity, production, supply, shortage, demand, competitor).
- Expanded corporate-event classification to include takeovers and deals.
- External European ticker symbols no longer reuse SEC identity mapping, avoiding collisions with unrelated US tickers.

## Performance / safety

- Mover Radar does not run on every one-second price tick.
- IBKR scanner/run pacing is respected at one request per second.
- IBKR scanner/params is cached for 15 minutes.
- News lookup is limited to the most interesting movers and is cached by company identity.
