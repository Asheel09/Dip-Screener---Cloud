# Meridian v2.2

- Adds real Interactive Brokers Client Portal Gateway quote mode (`MARKET_DATA_PROVIDER=ibkr`).
- Polls the current 12-symbol universe in a single batched IBKR snapshot request (default 500 ms).
- Loads 3 months of IBKR daily history on startup for 13-week high, week change, volatility and relative-volume inputs.
- Surfaces per-symbol IBKR market-data entitlement state (REALTIME / DELAYED / NOT_SUBSCRIBED / etc.).
- Fail-closed behavior: IBKR mode never substitutes synthetic prices when the gateway is unavailable.
- Adds live market-provider health details to Settings and API responses.
- Comparable-drop/recovery examples remain synthetic for now; Stage-7 historical analogue integration is the next step.
