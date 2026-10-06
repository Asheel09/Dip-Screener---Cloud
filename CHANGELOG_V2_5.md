# Meridian v2.5

- Fixes Client Portal Gateway WebSocket authentication by sending the `/tickle` session token before market-data subscriptions.
- Keeps SMART delayed snapshots only as a fallback when the exchange-specific stream does not return a quote marked `REALTIME` by IBKR field 6509.
- Resolves dotted US share-class symbols such as `BRK.B` and `BF.B` using IBKR's space-separated symbol format.
- No `.env` changes are required from v2.4.
