# Meridian v3.2

- Added authenticated local mover relay for free cloud deployments.
- Render remains the single dashboard URL; only one lightweight collector is needed on the work laptop.
- Fresh relay data takes priority over Render-side TradingView/Yahoo calls.
- Relay snapshots are explicitly marked stale after 10 minutes rather than masquerading as current.
- Relay traffic keeps the free Render service awake while in use.
