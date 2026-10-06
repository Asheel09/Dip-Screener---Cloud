# Meridian v3.1 Cloud

- Mover Radar now uses TradingView bulk screener as the primary cloud discovery source.
- Yahoo screener is retained only as a fallback because shared cloud IPs can be throttled with HTTP 429.
- US premarket ranking uses TradingView premarket change versus the previous regular close.
- Europe is scanned in one bulk Europe request rather than separate Yahoo country requests.
- No new API key is required.
