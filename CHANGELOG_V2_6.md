# Meridian v2.6

## Session-aware pricing
Meridian now chooses a US-equity market-data venue by Eastern Time session:
- OVERNIGHT: IBKR `OVERNIGHT` venue (8:00pm–3:50am ET, eligible US stocks/ETFs)
- PREMARKET: `EDGX` by default (4:00am–9:30am ET)
- REGULAR: configured free stream (IEX by default)
- AFTERHOURS: `EDGX` by default (4:00pm–8:00pm ET)
- SMART remains a fallback when the session-specific stream does not return a real-time quote.

The active session is carried with each quote and shown in the UI.

## Real recovery evidence
The old generated/placeholder recovery layer has been removed from IBKR mode. The historical worker now requests five years of real IBKR daily history and computes:
- non-overlapping comparable drawdowns
- 12-week forward return
- 12-week excess return versus real SPY history
- 75% drawdown recovery within 20 weeks
- maximum adverse excursion
- a sample-size-aware evidence score

A stock is `Pending` until processed. Fewer than four usable historical analogues are shown as `Insufficient`; Meridian does not invent a neutral 50.

## Cleaner company research
The free-source company drawer no longer dumps months of routine SEC filings. It keeps current GDELT-indexed company coverage and only SEC filings from the last 21 days, capped to five items. Generic boilerplate is hidden in the compact research cards.
