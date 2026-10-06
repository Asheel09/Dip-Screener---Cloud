# Meridian v3.3 — relay catalyst enrichment

- Mover Radar relay now enriches the largest drops with recent sourced company headlines locally.
- Local catalyst lookup uses GDELT first and Google News RSS as a fallback.
- Only classified recent headlines are shown as a Reason; otherwise the field remains blank.
- Relay news lookups are cached and run concurrently so the 90-second market scan is not serialized behind many news requests.
- Relay uploads now preserve `reason`, `cause_type`, `news_url`, source and timestamp.
- Mover Radar displays relay-supplied reasons immediately and skips redundant cloud-side reason lookup when one is already present.
