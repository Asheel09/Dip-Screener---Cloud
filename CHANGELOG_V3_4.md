# Meridian v3.4 — cleaner movers, safer catalysts

## Movers

- Renamed **Mover Radar** to **Movers**.
- Removed Europe from the active mover workflow. Meridian now scans US-listed names only.
- Added **both gainers and losers** to the same table. Default order is the largest absolute move first.
- Added quick filters for **All / Gainers / Losers** and **All US / NASDAQ / NYSE-American**.
- Added sortable mover columns and a filter drawer for move, price, market cap, relative volume and catalyst type.
- Sort controls cycle **Default → Desc → Asc → Default**.
- The personal relay enriches **every returned mover**, not only the top handful.

## Catalyst reliability

- Community-style first-person posts, emoji posts and common low-signal domains are rejected as catalysts.
- Common paywalled or unsuitable links are excluded, including Investing.com, WSJ, Bloomberg, FT, Barron's, MarketWatch, Seeking Alpha, TipRanks, Motley Fool, Zacks, Stocktwits and Reddit.
- Company relevance is checked before a GDELT result can be used.
- Direct publisher URLs are retained; Google News redirect links are no longer used by the relay.
- When no acceptable source supports a material event, the dashboard explicitly shows **No verified catalyst found** instead of guessing.

## Interface cleanup

- Removed **Live Dips**, **Comparable Drops**, **AI Research** and **Customize** from navigation.
- Rebuilt **Stock News** around current mover names rather than a generic market-news feed.
- Simplified **Broad Market** to major indexes, a compact market summary and clearly named sector ETFs.
- Added stronger hover, pressed and loading states to buttons, chips and sortable headers.
- **Refresh scan** now visibly enters a refreshing state and confirms successful refreshes.

## Recoverability

- A Movers recoverability score was deliberately **not added**. Current arbitrary-mover data does not provide enough consistent historical evidence to make a 0–100 score trustworthy. Existing historical recovery research remains available only where Meridian actually has sufficient price-history evidence.
