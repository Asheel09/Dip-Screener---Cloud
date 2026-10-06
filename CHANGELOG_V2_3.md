# Meridian v2.3

## Added
- Separate **Broad Market** tab.
- 49-instrument default universe: 28 company stocks + 21 market proxy ETFs.
- Broad market groups for indexes, sectors, semiconductors, long Treasuries, high-yield credit, gold, oil and the US dollar.
- Tracked-stock breadth, sector leadership/laggards and simple market-regime cards.
- 100% free public-source news pipeline using **GDELT + SEC EDGAR + Federal Reserve RSS + BLS RSS**.
- Persistent SQLite event store at `data/events.sqlite3`.
- `/api/news-feed` and `/api/market-overview` endpoints.
- Quote diagnostics from IBKR field `6509`, including explicit `REALTIME · NON-CONSOLIDATED`, `REALTIME · CONSOLIDATED`, or delayed badges when the gateway supplies them.
- Exchange diagnostics from IBKR bid/ask/last exchange fields.

## Changed
- `NEWS_PROVIDER=free` is now the default. OpenAI remains optional.
- Dip Detector excludes market-proxy ETFs; those are shown in Broad Market instead.
- IBKR status now distinguishes real-time consolidated vs non-consolidated quotes.
- UI assets bumped to v2.3.

## Important
The Web API `/iserver/marketdata/snapshot` endpoint is used as a readout from IBKR's open top-of-book streams; Meridian does not request paid regulatory snapshots. Which real-time feed you actually receive is determined by your IBKR entitlements and reported by field `6509`.
