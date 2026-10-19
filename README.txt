Meridian v3.6.1 UI cache hotfix

Upload/replace these files in the GitHub repo:
  app.py
  static/index.html
  static/app.js

Why: v3.6 backend deployed correctly, but index.html still referenced app.js?v=2.7, allowing browsers to serve the old UI. This patch changes the cache-busting version to 3.6.1 and tells the root HTML not to be cached.

After Render redeploys, /api/health should show 3.6.1. Then reload Meridian normally; if an already-open tab still shows the old table, use Ctrl+F5 once.
