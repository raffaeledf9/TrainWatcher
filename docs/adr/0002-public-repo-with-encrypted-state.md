# Public repository with encrypted state

Public repos get unlimited free Actions minutes; private ones (2,000 min/month) would only allow a run every ~30 min, and GitHub Pages (for the Mini App) is free only for public repos.

So the code is public and the bot's data (watches, prices, chat ids) lives **only** as an AES-256-encrypted SQLite file on a force-pushed orphan `state` branch. The key exists only as an Actions secret.

**Consequences:**
- Actions logs are public, so the job logs counts only.
- A secret scan runs before every push and in CI.
- No `pull_request_target` workflows.
