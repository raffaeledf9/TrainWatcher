-- Commands waiting for the GitHub job. A row is "taken" when the job pulls it and deleted on ack;
-- rows taken but not acked for 10 minutes are offered again (a crashed run loses nothing).
CREATE TABLE IF NOT EXISTS queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  kind TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}',
  taken_at INTEGER
);

-- Pre-rendered replies the job publishes after each run (status texts, chart PNGs as base64), read by
-- the Worker to answer status/chart requests instantly. key = "<user_id>:<kind>:<watch>".
CREATE TABLE IF NOT EXISTS snapshots (
  key TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  body TEXT NOT NULL
);

-- Small key/value facts: last dispatch, last successful run, watchdog state.
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
