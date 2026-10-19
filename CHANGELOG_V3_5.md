# Meridian v3.5

## Mover Radar context + persistence

- Keeps the last successful Mover Radar snapshot visible instead of blanking the table when the local relay stops.
- Saves a per-browser copy in `localStorage`; stale snapshots are clearly labelled `LAST SNAPSHOT` / `STALE`.
- Adds a best-effort server-side `data/relay_snapshot.json` cache (gitignored). Render free storage remains ephemeral, so browser persistence is the reliable zero-cost fallback.
- Changes the normal relay refresh cadence from 90 seconds to **1 hour**. Manual refresh still works.
- Changes Mover Radar browser auto-refresh from 2 minutes to **1 hour**.
- Extends the stale threshold to 90 minutes so an hourly relay does not immediately look stale.

## Better stock context

- Removes the Exchange column from Mover Radar.
- Adds **Sector**.
- Adds **Industry / exposures**. TradingView supplies sector and industry; Meridian adds a short list of thematic exposure tags such as AI, cloud, semiconductors, biotech, payments, energy, etc. These tags are context, not formal classifications.
- Adds **1W** and **1M** performance columns from TradingView (`Perf.W` and `Perf.1M`).
- Mover rows are sorted by absolute daily move so gainers and losers share one useful list.
- Renames the panel from “What fell hard today?” to “Largest movers” to match the current gainers + losers relay.

## Notes

- The last snapshot saved in a browser survives the relay stopping and a Render restart on that device.
- Browser cache is device/browser-specific. A phone will retain its own last snapshot after it has loaded one at least once.
- Cross-device persistence for a device that never saw the snapshot would require a real persistent cloud store; v3.5 deliberately avoids adding a paid dependency.
