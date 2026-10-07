# Meridian v3.4.1 — news/catalyst reliability hotfix

- Restores a second company-news discovery path using Google News RSS, while retaining GDELT.
- Screens the underlying publisher and continues to block Investing.com, paywalled/community sources and first-person junk.
- Keeps relevant company news even when it is not strong enough to be called the cause of a move.
- Movers now ask the server for company news whenever the relay has no verified reason or no news link.
- The relay now reports both `verified catalysts` and `with news` counts in Terminal for easy diagnosis.
- Reduces concurrent news lookups to avoid hammering free providers.
- Preserves latest accepted news metadata separately from a verified Reason.
