#!/usr/bin/env bash
# Idempotent setup/repair: internal secrets, D1, Worker deploy, Cloudflare + GitHub secrets, Telegram webhook.
# Needs: `wrangler login` done, `gh auth login` done (GH_CLI may point to gh.exe). Prints statuses only.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
REPO=raffaeledf9/TrainWatcher
GH="${GH_CLI:-gh}"
v() { python "$ROOT/scripts/setup.py" value "$1"; }

python scripts/setup.py gen
python scripts/setup.py check

cd worker
id=$(npx wrangler d1 list --json 2>/dev/null | python -c "import json,sys; print(next((d['uuid'] for d in json.load(sys.stdin) if d['name']=='trainwatcher'), ''))")
if [ -z "$id" ]; then
  npx wrangler d1 create trainwatcher >/dev/null 2>&1
  id=$(npx wrangler d1 list --json 2>/dev/null | python -c "import json,sys; print(next(d['uuid'] for d in json.load(sys.stdin) if d['name']=='trainwatcher'))")
fi
python ../scripts/setup.py d1-id "$id"
npx wrangler d1 execute trainwatcher --remote --file=schema.sql --yes >/dev/null 2>&1 && echo "d1: schema applied"

url=$(npx wrangler deploy 2>&1 | grep -o 'https://trainwatcher\.[a-z0-9-]*\.workers\.dev' | head -1)
[ -n "$url" ] || { echo "deploy failed"; exit 1; }
echo "worker deployed: $url"

for k in TELEGRAM_TOKEN TG_SECRET GH_TOKEN WORKER_SECRET OWNER_CHAT_ID; do
  v "$k" | npx wrangler secret put "$k" >/dev/null 2>&1 || { echo "worker secret $k FAILED"; exit 1; }
done
echo "worker secrets: 5 set"
cd ..

for k in TELEGRAM_TOKEN STATE_KEY WORKER_SECRET OWNER_CHAT_ID; do
  v "$k" | "$GH" secret set "$k" -R "$REPO" >/dev/null || { echo "github secret $k FAILED"; exit 1; }
done
"$GH" variable set WORKER_URL -R "$REPO" --body "$url" >/dev/null
echo "github: 4 secrets + WORKER_URL set"

python scripts/setup.py webhook "$url"
