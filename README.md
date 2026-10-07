# TrainWatcher

A personal Telegram bot that watches Trenitalia and Italo fares (mainly high-speed trains) and alerts when prices move.

- **Search** once, or **watch** a route for a day, a few days or a month: cheapest fare or specific fares
  (e.g. FrecciaYOUNG), one-way or round trip, with an optional max price.
- Alerts on new lows, rises (for fare watches), going under your max, sold out / back on sale, purchase-window deadlines.
  Quiet at night (23:00–07:00).
- English or Italian.

## How it runs (no server, no credit card)

| Part | Does |
|---|---|
| **GitHub Actions** (`.github/workflows/run.yml`) | Fetches prices (the operators block cloud IPs, GitHub runners work), sends alerts, keeps state AES-256-encrypted on the `state` branch. |
| **Cloudflare Worker** (`worker/`) | Telegram webhook with instant replies from D1 snapshots, serves the Mini App form (`webapp/`), starts a run on demand and whenever a watch is due (no watches, no runs besides GitHub's hourly fallback). |
| **Telegram** | The chat, the menu button that opens the form, the alerts. |

Why: [docs/adr](docs/adr). Glossary: [CONTEXT.md](CONTEXT.md).

Setup and repair: `bash scripts/setup.sh` (needs `wrangler login`, `gh auth login` and the tokens in `.env`, which is gitignored).
Tests: `python -m unittest`. Every push runs the tests and a secret scan over the whole history.

## Data sources

The station list (`webapp/stations.json`) contains data from Trenitalia S.p.A. published through the Italian National
Access Point, converted to GTFS by [deryclem/trenitalia-gtfs](https://github.com/deryclem/trenitalia-gtfs),
licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); details and changes in
[docs/data-sources.md](docs/data-sources.md). Fares come from the public booking sites of Trenitalia and Italo at run time.

TrainWatcher is a personal project, not affiliated with Trenitalia, Italo or the CCISS.
