# Meridian v3.6

## Mover context
- Adds **Rel Vol** from TradingView's 10-day relative-volume field.
- Adds **vs Sector**, calculated as the stock's current 1D mover value minus a liquid US sector ETF proxy (XLK/XLC/XLY/XLP/XLF/XLV/XLI/XLE/XLB/XLU/XLRE where a clean mapping exists).
- Adds a small **change since prior successful relay scan** line below 1D. Relay state is stored locally and ignored after 6 hours so a new day is not compared with a stale prior session.
- Adds source-based catalyst quality and catalyst age metadata under the Reason headline.
- Unverified placeholder reasons render as blank/— instead of looking like a real catalyst.

## Sorting and requests
- Mover headers are sortable independently of the main screener. Header cycle is Default → Asc → Desc → Default.
- Clicking **1D once** sorts the largest negative moves first, matching the usual dip-discovery workflow.
- Local-relay rows no longer trigger redundant Render-side reason lookups when the relay did not verify a catalyst. This avoids unnecessary requests and keeps the reason column cleaner.

## Refresh/persistence
- Hourly relay cadence and browser snapshot persistence from v3.5 remain unchanged.
