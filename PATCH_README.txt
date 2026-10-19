MERIDIAN v3.4.x -> v3.6 DIRECT PATCH

This patch is for the user's current pre-v3.5 setup (v3.4.x relay/cloud lineage).
You do NOT need to install v3.5 first.

Upload/replace these files in GitHub, preserving their paths:
  app.py
  meridian_relay.py
  services/market_stream.py
  static/app.js
  .gitignore
  CHANGELOG.md
  CHANGELOG_V3_5.md
  CHANGELOG_V3_6.md

The tests/ folder is optional for deployment but included for verification.

After committing to main, Render should auto-deploy. Check:
  https://YOUR-RENDER-URL.onrender.com/api/health
Expected version:
  3.6.0

Then replace your local relay with the included meridian_relay.py.
Your existing --url and --token command remains the same.
The relay defaults to a 3600-second (1 hour) refresh interval.

This direct patch includes all v3.5 and v3.6 changes:
- last-snapshot persistence when relay stops
- hourly relay/browser refresh
- remove Exchange column
- Sector + Industry/exposures
- 1W + 1M performance
- Relative Volume
- stock vs sector-proxy move
- change since prior relay scan
- catalyst source quality/age
- sortable Mover Radar headers
- cleaner blank handling for unverified catalyst reasons
