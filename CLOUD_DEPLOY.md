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
6. Wait for `/api/health` to return `"version":"3.1.0"`.
7. Open the generated `https://<name>.onrender.com` URL and log in.
8. Open the same URL on a second device (phone or personal laptop) and confirm Mover Radar loads.

## When the laptop files can be deleted

Do **not** delete your local Meridian folder immediately after the first deploy.

You can delete it after all four checks pass:

1. The Render deployment status is **Live**.
2. `https://<your-url>/api/health` reports `3.1.0`.
3. Mover Radar returns actual Yahoo US/European rows (not demo data).
4. You successfully log in and use the same URL from a **second device**.

At that point the laptop copy is no longer required to run Meridian. Keep the source repository: that becomes the master copy used for future updates.

## Free-host caveats

Render Free services can sleep after inactivity and their local filesystem is ephemeral. Meridian v3.0 treats local files as caches, so a restart can rebuild market/news data. Runtime UI settings can reset to packaged defaults after a restart/redeploy.
