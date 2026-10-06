# Meridian v2.4

- Expands the stock screening universe to 505 stocks plus 21 Broad Market proxies (526 instruments total).
- Uses a rotating IBKR snapshot window capped by `IBKR_ACTIVE_LINES` (default 90) so the tracked universe can exceed the account's simultaneous market-data-line allowance.
- Each rotation window is preflighted, warmed, read, then released with `/iserver/marketdata/unsubscribeall`.
- Priority stocks and Broad Market proxies are included in every rotation window; remaining stocks rotate through the spare slots.
- Adds progressive 3-month history enrichment at ~43 requests/minute, below IBKR's current 50 historical requests/minute cap.
- Caches resolved IBKR conids and recent history locally for faster restarts.
- Dip Detector only displays IBKR rows after history enrichment; Broad Market breadth can use all quoted stocks immediately.
- Default maximum visible dip rows increased from 100 to 250.
- Health/status now exposes tracked stocks, active line budget, scan progress, cycle count, and history-ready count.
- `IBKR_QUOTE_MODE=free_stream` now probes the official Client Portal WebSocket using `conid@IEX` for the complimentary US non-consolidated stream. Any symbol that does not return a real-time IEX quote falls back to SMART snapshots, so Meridian does not fabricate live status.
