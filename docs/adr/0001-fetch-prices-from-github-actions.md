# Fetch prices from GitHub Actions, not Cloudflare

Trenitalia (lefrecce.it) and Italo sit behind Akamai bot protection that answers **403 to Cloudflare Workers** but serves GitHub-hosted runners (tested 2026-10-06). Free always-on hosts without a card were closed, paid, sleeping or unproven. So all operator traffic runs in a GitHub Actions job (Python), dispatched on demand. Cloudflare only talks to Telegram, GitHub and its own D1.

**Consequences:**
- Price checks have run latency (≈ 30–60 s).
- GitHub's native `schedule` trigger is unreliable since 2026-08-26, so a Cloudflare cron dispatches runs and the hourly `schedule` is only a fallback.
