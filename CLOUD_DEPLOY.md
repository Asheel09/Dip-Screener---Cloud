# Deploy Meridian Cloud v3.0

Meridian v3.0 is designed to run once in the cloud. Your work laptop, personal laptop and phone only need a browser after deployment.

## Before deploying

You need a Render account and a Git repository containing this project. The project is already configured with `Dockerfile` and `render.yaml`.

### Required environment values

When Render asks for secret environment values, set:

- `SEC_USER_AGENT` = `Meridian your-real-email@example.com`
- `MERIDIAN_PASSWORD` = a password you choose for the dashboard

Do **not** put either value into a public repository.

The deployment already defaults to:

- `MARKET_DATA_PROVIDER=yahoo`
- `NEWS_PROVIDER=free`
- `YAHOO_CORE_REFRESH_SECONDS=300`

No IBKR Gateway is needed.

## Deploy on Render

1. Put this project into a Git repository.
2. In Render, create a new Blueprint/Web Service from that repository.
3. Render will read `render.yaml` and build the included Dockerfile.
4. Choose/keep the **Free** instance plan.
5. Enter `SEC_USER_AGENT` and `MERIDIAN_PASSWORD` when prompted.
6. Wait for `/api/health` to return `"version":"3.2.0"`.
7. Open the generated `https://<name>.onrender.com` URL and log in.
8. Open the same URL on a second device (phone or personal laptop) and confirm Mover Radar loads.

## When the laptop files can be deleted

Do **not** delete your local Meridian folder immediately after the first deploy.

You can delete it after all four checks pass:

1. The Render deployment status is **Live**.
2. `https://<your-url>/api/health` reports `3.2.0`.
3. Mover Radar returns actual Yahoo US/European rows (not demo data).
4. You successfully log in and use the same URL from a **second device**.

At that point the laptop copy is no longer required to run Meridian. Keep the source repository: that becomes the master copy used for future updates.

## Free-host caveats

Render Free services can sleep after inactivity and their local filesystem is ephemeral. Meridian v3.0 treats local files as caches, so a restart can rebuild market/news data. Runtime UI settings can reset to packaged defaults after a restart/redeploy.


## Local mover relay (free cloud workaround)

Render shared IPs can be rate-limited by anonymous Yahoo/TradingView endpoints. Meridian v3.2 can therefore accept mover snapshots from one lightweight local collector while the dashboard itself remains hosted on Render.

1. Add `MERIDIAN_RELAY_TOKEN` in Render Environment. Use a long random value.
2. Download only `meridian_relay.py` onto the work laptop.
3. Run:

```powershell
python meridian_relay.py --url https://YOUR-SERVICE.onrender.com --token YOUR_RELAY_TOKEN
```

The collector scans about every 90 seconds and uploads only mover rows. Phones and other computers still need only the Render URL. Stop it with Ctrl+C. If the relay stops, Meridian keeps the last snapshot and labels it stale after 10 minutes.
