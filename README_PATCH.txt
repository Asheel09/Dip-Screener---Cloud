Meridian v3.6.1 -> v3.6.2 patch

GitHub / Render:
Replace:
- app.py
- static/app.js
- static/index.html
- static/styles.css

Work laptop:
Replace your local relay with:
- meridian_relay.py

Your existing Render URL, MERIDIAN_RELAY_TOKEN and command do not change.
Default relay interval remains 3600 seconds (1 hour).
After Render deploys, /api/health should show version 3.6.2.
