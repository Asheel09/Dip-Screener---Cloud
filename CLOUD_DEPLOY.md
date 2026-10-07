# Deploy Meridian Cloud v3.4

The dashboard itself runs on Render. The personal Mac relay is optional but recommended for the freshest Movers snapshot and local catalyst enrichment.

## Render environment

Set these secrets/values on the Render service:

- `SEC_USER_AGENT` = `Meridian your-real-email@example.com`
- `MERIDIAN_PASSWORD` = your dashboard password
- `MERIDIAN_RELAY_TOKEN` = a long random relay token

Defaults remain `MARKET_DATA_PROVIDER=yahoo` and `NEWS_PROVIDER=free`. No OpenAI API key is required.

## Upgrade from v3.3

1. Copy the contents of `market_dashboard_v3_3_to_v3_4_cloud_patch.zip` over the v3.3 repository, replacing files when asked.
2. Commit and push the changed files.
3. Let Render redeploy.
4. Open `/api/health` and confirm `"version":"3.4.0"`.
5. Replace the old Mac relay with the supplied `meridian_relay_v3.py`. Keep it beside `start_meridian.command`.
6. Double-click `start_meridian.command`, then open Movers and press **Refresh scan** once to verify the new US gainers + losers feed.

## Full install

For a clean install, use `market_dashboard_v3_4_cloud.zip` as the repository contents, connect the repository to Render and configure the environment values above.

## What remains online when the Mac is off

Render continues serving the website, settings endpoints and cloud fallback market data. The local relay snapshot no longer updates while the Mac is asleep/off; Meridian labels a relay snapshot stale after 10 minutes.

## Data policy in v3.4

- Active Movers universe: US-listed equities only.
- Both positive and negative movers are collected.
- Free catalyst links are screened for company relevance and unsuitable/paywalled/community domains.
- No acceptable catalyst = **No verified catalyst found**.
