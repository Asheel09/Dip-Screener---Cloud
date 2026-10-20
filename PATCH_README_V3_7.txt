Meridian v3.6.2 -> v3.7.0 patch

Replace these files in the GitHub repo:
  app.py
  static/index.html
  static/app.js
  static/styles.css

Also replace the laptop relay with:
  meridian_relay.py

Your existing relay command still works. With no --interval argument, v3.7 uses:
  - 90-second scan starts during US regular trading hours (09:30-16:00 ET)
  - approximately 60-minute scan starts outside regular hours
  - a shorter pre-open wait when needed so it does not sleep through 09:30 ET

After Render deploys, /api/health should show 3.7.0.
Restart the relay process after replacing meridian_relay.py.
