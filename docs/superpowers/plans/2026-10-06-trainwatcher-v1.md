# TrainWatcher v1 — build plan

**Decisions:**
- The wayfinder map `.scratch/trainwatcher-v1/map.md` and its tickets hold the decisions; each round below names the tickets to re-read.
- The design spec `docs/superpowers/specs/2026-10-06-trainwatcher-design.md` is **partly superseded** by the map (fast path, entry points, search).
- Glossary: `CONTEXT.md`. Decision records: `docs/adr/`.

**Working rules**
- Rounds run **sequentially by the main session**, back-to-back.
- Each round ends with tests passing (`python -m unittest`), a commit, and a 3-line summary to the Owner.
- Stop only on a failing check, a blocked operator API, or a decision not covered here.
- **Owner actions are front-loaded in R0. Phone checks are batched in R9**, so R1–R8 run unattended.
- Python 3 stdlib only in the job, except matplotlib for charts. The Worker is plain JS (no framework). The Mini App is one static HTML file.
- Secrets never printed or committed. Logs are counts only. A secret scan runs before every push.

**Repo layout (target)**
```
trainwatcher/            Python job
  operators/trenitalia.py, operators/italo.py
  stations.py  model.py  store.py  schedule.py  alerts.py
  render.py  i18n.py  charts.py  queue.py  run.py  smoke.py
worker/                  Cloudflare Worker: src/index.js, schema.sql, wrangler.toml
webapp/                  Mini App: index.html, stations.json  (GitHub Pages)
scripts/                 build_stations.py, secret_scan.py, setup.py
tests/                   unittest + fixtures/ (captured responses, secrets stripped)
.github/workflows/       run.yml (dispatch + hourly fallback), ci.yml (tests + secret scan)
```

---

## R0 — Owner actions (front-loaded)

Tickets: One-time setup flow; Worker ↔ Telegram ↔ GitHub wiring facts; Fast path.

1. ✅ Telegram bot `@tr4inw4tcher_bot`; `TELEGRAM_TOKEN` and `OWNER_CHAT_ID` are in `.env`. ✅ In-App Browser off (Android).
2. **Cloudflare:** free sign-up (email only), then approve `wrangler login` in the browser. If asked, pick the `workers.dev` subdomain.
3. **GitHub token:** Claude first creates the **private** repo `raffaeledf9/TrainWatcher` (empty, nothing pushed). The Owner then opens the prefilled fine-grained token link, ticks *Only select repositories → TrainWatcher*, generates it and pastes it into `.env` as `GH_TOKEN`.
4. **Publish decisions:**
   - yes/no to making the repo public, which GitHub Pages needs for the form;
   - whether the planning notes (`.scratch/`, prototype branches) go public.
   - Claude flips the repo to public only after the secret scan passes.

**Publish decisions (Owner, 2026-10-06):**
- **Public: yes**, once the secret scan passes.
- **Planning notes: private.**
  - Before the first push, `.scratch/` is removed from the history that gets published, keeping a local-only archive.
  - The `prototype/*` branches are never pushed.

**Done when:** `wrangler whoami` shows the Owner's account, and `.env` holds `GH_TOKEN`. Claude verifies both without printing them.

## R1 — Tracer bullet + infrastructure

Tickets: Worker wiring; Fast path; Setup flow; Parallel requests from GitHub runners.

- Repo skeleton, `.gitignore`, `scripts/secret_scan.py` with a pre-push hook, `ci.yml`.
- **Secrets:**
  - `STATE_KEY` generated;
  - GitHub Actions secrets: `TELEGRAM_TOKEN`, `STATE_KEY`, `WORKER_SECRET`;
  - Worker secrets: `TELEGRAM_TOKEN`, `GH_TOKEN`, `WORKER_SECRET`, `OWNER_CHAT_ID`.
- **D1 database:** `queue` and `snapshots` tables.
- **Worker:**
  - `/tg` webhook (checks the secret header, owner lock, always returns 200);
  - `/job/*` API for the job (pull and ack the queue, push snapshots; shared secret);
  - per-minute cron (5-min tick dispatch + watchdog).
  - `setWebhook` with `secret_token`; the old getUpdates path is retired.
- **`run.yml`:** workflow_dispatch + hourly schedule fallback, concurrency group, `state` orphan branch encrypted with AES-256.
- **Minimal job:** pull the queue → for `/try`, run a hard-coded Milano C.le → Roma Termini search on both operators → reply with the cheapest price.
- **Checks:**
  - `/start` answered by the Worker in **< 2 s**;
  - `/try` result in **≈ 30–60 s**;
  - measure the webhook push latency;
  - the run log has no routes, prices or ids;
  - the secret scan is clean.

## R2 — Operator connectors

Tickets: Trenitalia full-day cost; Italo session limits; Same-day A/R; Young/senior fares; Failure detection.

- **Trenitalia:** `ticket/solutions` paging (10 per page, stop on a short page), gzip, window-limited paging, `frecceOnly`+`noChanges`. For same-day A/R: `returnDepartureTime` plus a reverse-direction search for the return leg.
- **Italo:** anonymous login (`X-Anonymous-User`) → working-session → booking → poll → **delete**. Search by passenger type (`youngPassengers`/`seniorPassengers`); round-trip search for same-day A/R.
- **Shared:**
  - concurrency of 5 (Trenitalia) and 3 (Italo), halved on 403/429;
  - classify each result as OK / EMPTY / BLOCKED / BROKEN, with the canary route.
- **Checks:** parser tests on captured fixtures; `python -m trainwatcher.smoke` succeeds on both operators from a GitHub run.

## R3 — Station catalogue

Ticket: Station catalogue.

- `scripts/build_stations.py`:
  - names and Frecce flags from trenitalia.com `cruscotto-stations.json`;
  - ids from the GTFS StopPlace suffix (CC-BY);
  - Italo `/stations`, matched by coordinates within 500 m;
  - city groups from both operators;
  - a curated **abbreviation table** (MIL, ROM, TOR, NAP…).
- Output `webapp/stations.json`, about 12–15 KB. The resolver works by id; free-text lookup is never used at run time.
- **Checks:** known stations and city groups resolve on both operators; Italo-only stations are flagged; the file size is under the limit.

## R4 — Core logic and state

Tickets: Watch and alert semantics; Run budget and check cadence; Multi-user effort.

- **Model:**
  - Watch (Cheapest / Fare), Legs (days: one / range / month; time window: any, presets, custom);
  - Passenger type, operators, classes, max price, the rise-alerts switch, Past watches.
  - **`user_id` everywhere** (multi-user-ready; Owner-only access).
- **Store:** SQLite; observations stored only on change; the `last` table; history kept forever, `/forget` per watch.
- **Prices:** best price per train, lowest price per leg, ties, round-trip totals (one-way vs A/R families).
- **Scheduler:**
  - check cadence 15 min / 30 min / 2 h / 6 h depending on how close the first travel day is;
  - 3 min fetch budget per run; nearest departure first, no watch skipped twice;
  - load warning above 70 %.
- **Checks:** scenario tests; encrypted state survives a save → load round trip in CI.

## R5 — Alerts

Tickets: Watch and alert semantics; Young/senior fares; Bot messages (alert rules).

- **Kinds:** Drop, Rise, Under max, Back in stock, Gone, Still on sale, Last day to buy, Round trip.
  - **Gone only after a live Check confirms** the fare is unavailable on every train.
  - **Still on sale** fires when a live Check finds the fare on sale after the purchase window should have closed.
- **Dedup:** Drop alerts only below the lowest price already alerted. Fare watches alert on every change of the lowest. The under-max alert re-arms after the price goes back above max.
- **Max price:** gates Cheapest watches only. Fare watch alerts are marked ✅/⚠️.
- Multi-day watches alert only when the watch's lowest price changes; round trips work on the total. One message per watch per run; silent from 23:00 to 07:00.
- **Checks:** a scenario table covering every kind, including gone-confirm and still-on-sale.

## R6 — Messages, charts, fast replies

Tickets: Bot messages and buttons; Fast path; Watch and alert semantics (language).

- **Templates (EN/IT) exactly as approved:**
  - status style A with "🔄 last check HH:MM";
  - alerts in C + A layout with struck-through old prices;
  - `/list` L2 v2;
  - ack, search result, delete confirmation, `/past`, `/help`.
- **Language:** first `/start` shows 🇮🇹/🇬🇧 buttons pre-selected from `language_code`; `/language` changes it; stored per user.
- **Charts:** one line per train, legend "IT 9967  06:15 → 09:24", € on every tick, price labels inside the plot, max line.
- **Snapshots:** the job pushes pre-rendered status texts and chart PNGs to D1 (base64 TEXT, one row per watch per run). The Worker answers 📋/📈/list/delete-confirm in about 1 s; 🔄 Check now is acked instantly and dispatched.
- **Checks:** golden-text tests per template and language; Worker unit tests (secret header, owner lock, callback routing); measured Worker CPU per chart reply.

## R7 — Mini App (form) and Search

Tickets: New-watch form prototype; Bot messages (entry points); Fast path; Station catalogue.

- `webapp/index.html` = the approved form (branch `prototype/new-watch-form`, variant D v5, plus the Search/Watch switch from `prototype/bot-messages`).
  - It is rewritten properly: validate on submit, calendar popover, chip popovers, review sheet, EN/IT, Telegram theme.
- Hosted on **GitHub Pages**. Menu button **"🚄 TrainWatcher"** set for the Owner's chat.
- Submit = `text/plain` POST to the Worker's `/form` with `initData`. The Worker checks the HMAC (and settles the `signature` question), the age of `auth_date` and the owner, then enqueues and dispatches.
- **Search:** a one-off Check whose result offers **"➕ Watch this"**.
- **Checks:** initData tests with real data captured from the Owner's first submit; payload ≤ 4 KB; a search and a watch created end-to-end, triggered by Claude via the Worker test route.

## R8 — Running unattended

Tickets: Telling 'no trains' from 'API broken'; Setup flow; Run budget.

- Failure notices to the Owner after 3 failing runs, plus a recovery notice; "⚠️ prices from HH:MM" on status views while failing.
- Weekly silent self-check (Monday 09:00).
- Dead-man switches: the Worker watches the job, and the job watches the Worker.
- GitHub token expiry reminder (14 days before).
- `ci.yml`: tests + secret scan on every push; README with CC-BY attribution for the GTFS-derived data.
- Delete `.env` after confirming all secrets are uploaded (the Owner keeps tokens only in the platforms).
- **Checks:** simulated operator failure → notice; simulated Worker silence → job notice; CI green.

## R9 — Owner acceptance on the phone (batched, ~20 min)

- Menu button and form usability, including the calendar and touch targets inside Telegram.
- Status/chart speed and webhook latency.
- Alert appearance, using forced test alerts.
- Links open the websites.
- Language switching.
- Fixes found here become a small R10.
