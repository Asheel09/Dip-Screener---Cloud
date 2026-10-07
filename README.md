# Meridian Market Dashboard v3.4 Cloud

Meridian is a cloud-hosted market-move dashboard with a lightweight optional personal-laptop relay. The Render URL stays online independently; the relay supplies fresher US mover discovery and verified catalyst links while it is running.

## What v3.4 is optimized for

The main question is **“What is moving hard today, and is there a verified reason?”**

- **Movers** scans US-listed stocks and combines large gainers and losers in one table.
- **Catalyst checks** use free public sources and reject common paywalls, community posts and obviously irrelevant headlines.
- Every mover has an explicit status: a sourced catalyst or **No verified catalyst found**.
- **Stock News** is company-specific and starts from the current Movers list.
- **Broad Market** is deliberately compact: major index ETFs plus clearly labelled US sector ETFs.
- **Watchlist** and **Backtests** remain available.

Removed from navigation in v3.4: Live Dips, Comparable Drops, AI Research and Customize.

## Mover universe and sorting

The active mover workflow is US-only. It includes major NASDAQ, NYSE and NYSE-American families rather than hard-locking the dashboard to NASDAQ, because relevant US names can trade on either major exchange. Use the exchange chips if you only want NASDAQ.

Movers includes both positive and negative moves. Default order is the largest absolute move first. The Move column cycles **Default → Desc → Asc → Default**, so Desc puts the largest gainers first and Asc puts the largest losers first.

## Catalyst source policy

The personal relay uses GDELT to discover direct publisher URLs, then applies relevance and source-quality checks. It rejects common paywalled/low-signal/community domains such as Investing.com, WSJ, Bloomberg, FT, Barron's, MarketWatch, Seeking Alpha, TipRanks, Motley Fool, Zacks, Stocktwits and Reddit.

A headline must actually relate to the company before it can become the stated reason. First-person/community-style text is rejected. If no suitable material catalyst is found, Meridian does not invent one.

## Recoverability

There is no recoverability bar in the Movers table in v3.4. A single number would look precise without being reliable for arbitrary newly discovered stocks. Historical recovery evidence is still shown where Meridian has enough real history, but it is not generalized into a score for every mover.

## Cloud defaults

```env
MARKET_DATA_PROVIDER=yahoo
NEWS_PROVIDER=free
YAHOO_CORE_REFRESH_SECONDS=300
SEC_USER_AGENT=Meridian your-real-email@example.com
MERIDIAN_PASSWORD=choose-a-password
MERIDIAN_RELAY_TOKEN=use-a-long-random-value
```

No OpenAI API key is required for the v3.4 UI.

## Personal Mac relay

Put the updated `meridian_relay_v3.py` beside your `start_meridian.command` launcher. The launcher can continue using the same Render URL and relay token.

Manual Terminal launch is also supported:

```bash
export MERIDIAN_URL="https://YOUR-SERVICE.onrender.com"
export MERIDIAN_RELAY_TOKEN="YOUR_RELAY_TOKEN"
python3 meridian_relay_v3.py
```

The relay scans about every 90 seconds. If the Mac sleeps or the Terminal process stops, the Render site remains available but the mover snapshot stops refreshing and is marked stale after 10 minutes.

## Deploying the site update

Replace the v3.3 project files with the v3.4 patch (or use the full v3.4 folder), commit and push to the private Git repository connected to Render. After deployment, `/api/health` should report version `3.4.1`.

## Tests

```bash
PYTHONPATH=. pytest -q
```

The v3.4 package includes tests for the trimmed navigation, US-only two-sided mover scan, relay filtering and catalyst-source safeguards.
