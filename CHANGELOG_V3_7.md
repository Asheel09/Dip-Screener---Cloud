# Meridian v3.7.0

- Adaptive mover cadence: 90-second scan starts during US regular trading hours (09:30–16:00 ET), roughly hourly outside regular hours, with a wake-up guard so the relay does not sleep through the opening bell.
- Browser Mover Radar follows the same adaptive cadence: 90 seconds during regular hours, 60 minutes outside.
- Refresh button now has hover, pressed, disabled and visible `Refreshing…` spinner states, plus completion/error feedback.
- Mover header shows snapshot age (`DATA · 4M OLD` / `STALE · ...`) instead of misleading `LIVE · 1s` while viewing the radar.
- Repeated per-row `US`, `PREVIOUS_CLOSE`, and `Local TradingView relay` labels were removed; the common session/source are shown once in the compact table header. Non-US region labels remain available for future international rows.
- Relative volume now renders zero/unavailable values as `—` and uses an explicit `HIGH` badge for >=2× rather than an unexplained `!`.
- Catalyst filtering rejects several common low-signal SEO/opinion/legal-ad headlines (competitor lists, stock forecasts, price predictions, bargain/better-buy articles, and law-firm investor solicitation notices).
- During regular hours, cached catalyst checks expire after 10 minutes instead of one hour; new mover symbols are still checked immediately.
- Relay diagnostics now print separate Sector / Industry / 1W / 1M / Rel Vol population counts.
- Version bumped to 3.7.0 and static assets cache-busted.
