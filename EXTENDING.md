# Extending Meridian without breaking it

## Add a sidebar screener
1. Add metadata to `config/screeners.json`.
2. Add the backend service in `services/`.
3. Add an `/api/...` route in `app.py`.
4. Add one view function in `static/app.js` (or move views to modules when the dashboard grows).

## Change update speed
Set `UI_PUSH_MS=500`, `1000`, `2000`, etc. The market-data provider can tick more frequently than the browser push interval.

## Change market-data provider
Implement the provider in `services/market_stream.py` or add a new provider class. Do not put provider-specific logic into the WebSocket or UI.

## Change news provider
Implement it only in `services/news_service.py`, returning the normalized news schema.

## Add persistence
Replace localStorage watchlists with a database-backed `/api/watchlist` service. PostgreSQL is a natural production option.

## Add background research jobs
For expensive catalyst/historical analysis, add Redis + a worker queue. Live prices should never wait for news searches or backtests.

## Add alerts
Once signals are stable, create an alerts service consuming the same live state. Alerts should be event-driven (new severity threshold, new material catalyst, research state changed) rather than tied to page refreshes.
