# TrainWatcher — design

Telegram bot that watches Trenitalia (Frecce) and Italo fares over time and alerts on price drops.

## Constraints (verified 2026-10-06)

- No credit card, no home PC, free.
- Trenitalia `lefrecce.it` and Italo `api-biglietti.italotreno.com` sit behind Akamai bot protection.
  - Cloudflare Workers egress: **403 on both** (blocked).
  - GitHub Actions runners (Azure): **200 on both** (works).
- GitHub's native `schedule` trigger is unreliable since 2026-08-26 (runs delayed 4–6 h or dropped).
  `workflow_dispatch` via API starts within seconds.

## Architecture

> **Superseded in part (2026-10-06):** Telegram now uses a **webhook to the Worker** (not a 1-minute peek), with Cloudflare **D1** for the command queue and per-run snapshots, so status/chart replies take ≈ 1 s. ➕ New watch is a **menu-button** Mini App posting to the Worker. See map decision "Fast path: instant status and chart replies".

```
Telegram ◄──getUpdates/send──┐
   ▲                         │
   │ peek (every min)        │
Cloudflare Worker ──workflow_dispatch──► GitHub Actions job (Python)
 (cron * * * * *)                          ├─ Trenitalia API
                                           ├─ Italo API
                                           └─ state: encrypted SQLite on `state` branch
```

- **Worker** (`worker/`, JS, ~40 lines), cron every minute:
  1. Peek Telegram `getUpdates` (no offset, `timeout=0`) — pending commands?
  2. Is a check tick due (every 5 min)?
  3. If either, and no run is queued/in progress, dispatch `run.yml`.
  - Never touches train APIs. Secrets: `TELEGRAM_TOKEN`, `GH_TOKEN` (fine-grained, Actions:write on this repo only).
- **Job** (`.github/workflows/run.yml`): `workflow_dispatch` + hourly `schedule` as fallback if the Worker dies.
  `concurrency` group so runs never overlap. One run ≈ 30 s–4 min:
  1. Decrypt `state.db` from the `state` branch.
  2. Consume Telegram updates, answer commands.
  3. Run due price checks (time budget ~3 min), send alerts.
  4. Encrypt and force-push a single orphan commit to `state` (no history growth).
- **GitHub Pages** serves the Mini App form (`webapp/`) as static files only; it holds no user data.
- **Repo is public** (unlimited free minutes). Privacy: state is AES-256 encrypted (`openssl enc -aes-256-cbc -pbkdf2`, key in Actions secret `STATE_KEY`); logs print counts only, never routes, dates or chat ids.

Latency: command → reply/price result ≈ 40–90 s. Price checks every 5 min at the finest.

## Operator adapters (`trainwatcher/trenitalia.py`, `trainwatcher/italo.py`)

Both return a flat list of `Offer(operator, train, dep, arr, cls, fare, price, seats)`, where `seats` is the available count, or `None` if unknown.

- **Trenitalia** — `POST https://www.lefrecce.it/Channels.Website.BFF.WEB/website/ticket/solutions`
  - Body: `departureLocationId`, `arrivalLocationId`, `departureTime`, `adults: 1`, `criteria{frecceOnly, noChanges, limit, offset}`.
  - Paginate with `offset` until results pass the end of the day.
  - Fares: `grids[].services[]` (class: STANDARD/PREMIUM/BUSINESS/EXECUTIVE) → `offers[]` (`name`: Base, Economy, Super Economy, FrecciaYOUNG, FrecciaSENIOR…, `price.amount`).
  - Stations: `GET .../website/locations/search?name=&limit=`.
- **Italo** — anonymous flow, no credentials:
  1. `POST https://biglietti.italotreno.com/api/login` with header `X-Anonymous-User: true`, body `{"isAnonymous":true}` → cookie `BIGSessionToken` (JWT, ~1 h).
  2. `POST https://api-biglietti.italotreno.com/api/v1/working-sessions` with `Authorization: Bearer`, `X-BIG-working-session-id: <uuid4>`.
  3. `POST /api/v1/booking` with `departureStation`, `arrivalStation`, `departureDate`, `adultPassengers`/`youngPassengers`… → `operationId`.
  4. Poll `GET /api/v1/booking/status/{operationId}` until 200.
  5. `DELETE /api/v1/booking/{bookingId}` and poll `.../delete/status/{op}`. A session allows **one open booking**: without this step the next search fails with `toomanyconnections`.
  - Round trip: the same body with `isRoundTrip: true` and `returnDate`. The response has two trips, `forward` and `backward`.
  - Fares: `trips[0].travelSolutions[].journeys[].segments[].fares[]` (`productClass` S/P/C/S2 → Smart/Prima/Club/Salotto, `offerType` Flex/Economy/Low Cost/eXtra Magic…, `paxFares[0].singlePaxFarePrice`, `availableCount`).
  - Stations: `GET /api/v1/stations` (cached in state for 7 days).
- Polite pacing: ≥0.5 s between requests, browser User-Agent, one Italo session per run.
- Young fares: Trenitalia offers are filtered by fare name (`FrecciaYOUNG`). Italo is searched with `youngPassengers: 1` when the watch asks for young fares.

## Language

The bot and the Mini App are **bilingual (English / Italian)**; the Owner selects the language. All bot messages, buttons and form labels exist in both languages. Station names stay as the operators publish them. Fare names keep their official spelling (e.g. FrecciaYOUNG, Low Cost). How the language is picked and stored is decided in the plan.

## Watches: GUI (primary)

A **Telegram Mini App** (static page `webapp/index.html`, hosted on GitHub Pages of this repo) opens from a persistent reply-keyboard button **"➕ New watch"**. The form runs entirely on the phone (instant). On submit, `Telegram.WebApp.sendData(json)` delivers it to the bot as a `web_app_data` message, which is handled at the next run.

Form fields:

- **From / To**: text with autocomplete from a static `webapp/stations.json`. The file is the union of both operators' high-speed stations and city groups, regenerated by a script. Names are resolved to operator codes server-side, and the reply echoes the resolution.
- **Trip**: one way | round trip.
- **Per leg** (outbound, plus return for a round trip):
  - **Days**: one day | several days (from–to) | whole month. Uses native date/month inputs.
  - **Departure time**: any, or between HH:MM and HH:MM. Setting both to the same value means one exact train.
- **Fare**: any | Base | Economy | Super Economy | FrecciaYOUNG | FrecciaSENIOR | Italo Flex | Italo Economy | Italo Low Cost | Italo eXtra Magic | Italo Young | A/R same day.
- **Class**: any | Standard | Premium | Business | Executive | Smart | Prima | Club.
- **Operators**: Trenitalia and/or Italo.
- **Max price** (optional): per leg for one-way trips, total for round trips.

Validation runs in the form (dates in the future, from ≠ to, return after outbound). The payload is ≤ 4 KB JSON, validated again server-side.

Existing watches are managed with **inline buttons** under `/list`: 📋 Status (last snapshot) · 📈 History · 🔄 Check now (fresh fetch, then status) · 🗑 Delete. Each press is answered at the next run (≤ ~90 s).

Round trip = two legs stored under one watch. Reports show the best outbound + best return = best total.

**Round-trip pricing** (verified 2026-10-06, Milano→Roma, 10 Nov): both operators price all normal fares identically in one-way and round-trip searches. The only difference is an extra **same-day return fare**:

| Operator | Same-day fare | Price seen |
|---|---|---|
| Trenitalia | `A/R IN GIORNATA` | €66.50/leg vs Base €95 |
| Italo | `Andata e Ritorno` | €59.90–62.90 vs Flex €89.90 |

Therefore:

- Legs on **different days** → two one-way searches. Nothing is lost.
- Legs on the **same day** → round-trip search, so the A/R fare is included:
  - Trenitalia: `returnDepartureTime` in the body. The return leg is searched in the reverse direction the same way.
  - Italo: `isRoundTrip` + `returnDate`; one search returns both legs.
  - The A/R fare is valid only if both legs use it. The total is computed per fare family: the min of (best one-way out + best one-way back, best A/R out + best A/R back).
- The fare option **"A/R same day"** is added to the form.

## Watches: text commands (fallback)

Single-line commands, because each message costs a run:

```
/watch <from> > <to> <when> [HH:MM | HH:MM-HH:MM] [return=<when> [HH:MM | HH:MM-HH:MM]] [train=9611] [fare=young] [class=standard] [max=40] [op=italo]
  <when>: 10/11 | 10/11/2026 | 10/11-16/11 (range, e.g. a week) | 11/2026 (month)
/list                 active watches with current best price
/del <id>             remove a watch
/history <id>         PNG chart: best price over time (per train when the watch is a single train)
/now <id>             check this watch in the current run
/help
```

- Station names are matched fuzzily against both operators' station lists. The reply echoes the resolution, e.g. "Milano Centrale → Roma Termini".
- `fare=`/`class=` are case-insensitive substring matches. `op` defaults to both operators.
- Watches auto-expire after their last date.
- **Owner lock:** the first chat to send `/start` becomes the owner; all other chats are ignored.

## Checks, history, alerts

- Cadence by days until the first departure date: ≤3 d → 15 min, ≤14 d → 30 min, ≤45 d → 2 h, else 6 h. `/now` overrides.
- An observation row is stored only when an offer's price or seat availability changes. The key is (operator, train, departure, class, fare).
- **Per train**, "best price" = the minimum over offers matching the watch's filters. The watch's **lowest price** = the minimum over its trains.
- **Ties:** every report (baseline, alerts, `/list`) shows the lowest price followed by **all** trains at that price (operator, date, time, train number, fare). For round trips this applies per leg.
- **Status view (on request, ignores `max`)**, shown on watch creation, 📋 Status and 🔄 Check now:
  - the 5 cheapest trains, plus any further trains tied at the lowest price;
  - each line: price, operator, date, time, train number, fare, class, seats left if known;
  - if everything is above `max`, the view says so ("lowest €52, your max €40"), but the trains are still listed.
  - `max` affects **push alerts only**.
- Alerts, grouped into one message per watch per run:
  - the first check sends the status view (below) as a baseline;
  - the best price dropped by ≥ €1 since it was last seen;
  - the best price is now ≤ `max` (only once per crossing);
  - matching offers were sold out and are available again.
- If `max` is set, drop alerts are sent only when the price is ≤ `max`.
- Messages include a booking link (lefrecce.it / italotreno.com).

## Data (SQLite)

`watches`, `observations` (change-only), `last` (current value per key), `meta` (telegram offset, owner chat id, station caches).

## Errors

- An operator error (403, 5xx, schema change) is logged as a count. After 3 consecutive failing runs the owner gets one Telegram notice, and another one when it recovers.
- A watch failing does not stop the other watches.
- State is pushed only after a run completes; a crashed run loses nothing older than that run.

## Testing

- `unittest`, stdlib only. Fixtures are real JSON responses captured on 2026-10-06 (secrets stripped).
- Covered: both parsers, command parser, alert diffing, cadence.
- A canary search per operator (Milano C.le → Roma Termini, about 5 weeks ahead) runs whenever results look suspicious (`check.canary`).

## One-time setup (user)

1. @BotFather → new bot → token.
2. Free Cloudflare account + `wrangler login`.
3. Fine-grained GitHub token (prefilled link): Actions read/write on `TrainWatcher` only.
4. Paste tokens into the prompts of a setup script; they never pass through chat.

Everything else (repo, secrets generation, deploys, webhook-free wiring) is automated.

## Out of scope (v1)

- Instant webhook replies (needs a queue).
- Multiple users.
- Promotional round-trip offers that did not appear in the API on 2026-10-06 (e.g. Italo "A/R Magic"). They are picked up automatically as fare names if they show up in search results.
- Regional trains.
- Planes.
