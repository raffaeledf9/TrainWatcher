"""SQLite state (encrypted at rest by the workflow, ADR 0002). Multi-user-ready: everything is per user_id,
access is Owner-only for now. Observations are stored only when an Offer's price or availability changes (not its
seats left: that was 95 % of the rows and would push the state file towards GitHub's size limit), and offers
that vanish from a successful Check are recorded as unavailable (price NULL)."""
import json
import sqlite3
from datetime import datetime

from trainwatcher.model import Watch

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, lang TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS watches (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, spec TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL,
  last_check TEXT, next_check TEXT, skipped INTEGER NOT NULL DEFAULT 0, alert_state TEXT NOT NULL DEFAULT '{}');
-- current value per offer, scoped by the search unit that produced it (passenger type changes fares)
CREATE TABLE IF NOT EXISTS offers_last (
  key TEXT PRIMARY KEY, unit TEXT NOT NULL, offer TEXT NOT NULL, price REAL, seats INTEGER, seen_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS offers_last_unit ON offers_last(unit);
CREATE TABLE IF NOT EXISTS observations (key TEXT NOT NULL, at TEXT NOT NULL, price REAL, seats INTEGER);
CREATE INDEX IF NOT EXISTS observations_key ON observations(key, at);
"""


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def iso(dt):
    return dt.isoformat(timespec="seconds")


# ---------- users ----------
def user_lang(db, user_id):
    r = db.execute("SELECT lang FROM users WHERE id = ?", (user_id,)).fetchone()
    return r["lang"] if r else None


def set_user_lang(db, user_id, lang, now):
    db.execute("INSERT INTO users (id, lang, created_at) VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET lang = excluded.lang",
               (user_id, lang, iso(now)))
    db.commit()


# ---------- watches ----------
def _watch(r):
    w = Watch.from_json(json.loads(r["spec"]))
    w.id, w.status = r["id"], r["status"]
    w.last_check = datetime.fromisoformat(r["last_check"]) if r["last_check"] else None
    w.skipped = bool(r["skipped"])
    w.alert_state = json.loads(r["alert_state"])
    return w


def add_watch(db, watch, now):
    spec = watch.to_json()
    spec.pop("id", None)
    cur = db.execute("INSERT INTO watches (user_id, spec, created_at, next_check) VALUES (?, ?, ?, ?)",
                     (watch.user_id, json.dumps(spec), iso(now), iso(now)))
    db.commit()
    return cur.lastrowid


def get_watch(db, watch_id):
    r = db.execute("SELECT * FROM watches WHERE id = ?", (watch_id,)).fetchone()
    return _watch(r) if r else None


def watches(db, user_id=None, status="active"):
    q, args = "SELECT * FROM watches WHERE status = ?", [status]
    if user_id is not None:
        q, args = q + " AND user_id = ?", args + [user_id]
    return [_watch(r) for r in db.execute(q + " ORDER BY id", args)]


def due_watches(db, now):
    return [_watch(r) for r in db.execute("SELECT * FROM watches WHERE status = 'active' AND next_check <= ? ORDER BY id", (iso(now),))]


def mark_checked(db, watch, now, next_at, alert_state):
    db.execute("UPDATE watches SET last_check = ?, next_check = ?, skipped = 0, alert_state = ? WHERE id = ?",
               (iso(now), iso(next_at), json.dumps(alert_state), watch.id))


def mark_skipped(db, watch):
    db.execute("UPDATE watches SET skipped = 1 WHERE id = ?", (watch.id,))


def set_status(db, watch_id, status):
    db.execute("UPDATE watches SET status = ? WHERE id = ?", (status, watch_id))
    db.commit()


def forget(db, watch_id):
    """/forget: the watch disappears; offer history stays (it is shared by route, not owned by a watch)."""
    db.execute("DELETE FROM watches WHERE id = ?", (watch_id,))
    db.commit()


# ---------- offers and history ----------
def unit_id(unit):
    op, a, b, day, pax, ret = unit
    return f"{op}|{a}|{b}|{day}|{pax}|{ret or ''}"


def offer_key(unit, offer):
    return unit_id(unit) + "|" + "|".join(str(x) for x in offer.key)


def record(db, unit, offers, now):
    """Store a successful Check of one unit. Returns (changed, vanished) offer keys."""
    uid, at = unit_id(unit), iso(now)
    seen, changed = set(), []
    for o in offers:
        k = offer_key(unit, o)
        seen.add(k)
        r = db.execute("SELECT price, seats FROM offers_last WHERE key = ?", (k,)).fetchone()
        if r is None or r["price"] != o.price:
            db.execute("INSERT INTO observations (key, at, price, seats) VALUES (?, ?, ?, ?)", (k, at, o.price, o.seats))
            changed.append(k)
        db.execute("INSERT INTO offers_last (key, unit, offer, price, seats, seen_at) VALUES (?, ?, ?, ?, ?, ?) "
                   "ON CONFLICT(key) DO UPDATE SET price = excluded.price, seats = excluded.seats, seen_at = excluded.seen_at, offer = excluded.offer",
                   (k, uid, json.dumps(o.__dict__), o.price, o.seats, at))
    vanished = []
    for r in db.execute("SELECT key FROM offers_last WHERE unit = ? AND price IS NOT NULL", (uid,)).fetchall():
        if r["key"] not in seen:
            db.execute("UPDATE offers_last SET price = NULL, seats = NULL, seen_at = ? WHERE key = ?", (at, r["key"]))
            db.execute("INSERT INTO observations (key, at, price, seats) VALUES (?, ?, NULL, NULL)", (r["key"], at))
            vanished.append(r["key"])
    db.commit()
    return changed, vanished


def prune(db, today, search_days=7):
    """Forget what is never read again, once a day, and compact the file: current offers and price history of days
    already past (a finished watch keeps its last chart in the Worker), and searches whose "Watch this" is older than
    search_days. A one-month search alone records ~50,000 history rows: kept, they'd outgrow GitHub's file limit.
    -> rows dropped."""
    if meta_get(db, "pruned_on") == str(today):
        return 0
    day = lambda key: key.split("|")[3]
    old_units = [u for (u,) in db.execute("SELECT DISTINCT unit FROM offers_last") if day(u) < str(today)]
    old_keys = [k for (k,) in db.execute("SELECT DISTINCT key FROM observations") if day(k) < str(today)]
    cutoff = int(datetime.combine(today, datetime.min.time()).timestamp()) - search_days * 86400
    old_searches = [k for (k,) in db.execute("SELECT k FROM meta WHERE k LIKE 'search:%'") if int(k[7:17]) < cutoff]
    db.executemany("DELETE FROM offers_last WHERE unit = ?", [(u,) for u in old_units])
    db.executemany("DELETE FROM observations WHERE key = ?", [(k,) for k in old_keys])
    db.executemany("DELETE FROM meta WHERE k = ?", [(k,) for k in old_searches])
    meta_set(db, "pruned_on", today)  # commits
    if old_units or old_keys or old_searches:
        db.execute("VACUUM")
    return len(old_units) + len(old_keys) + len(old_searches)


def current_offers(db, unit):
    """Offers available now for a unit (as Offer dicts), from the last successful Check."""
    return [json.loads(r["offer"]) for r in db.execute("SELECT offer FROM offers_last WHERE unit = ? AND price IS NOT NULL", (unit_id(unit),))]


def history(db, keys):
    """{key: [(at, price_or_None), ...]} oldest first, for charts."""
    out = {k: [] for k in keys}
    for k in keys:
        out[k] = [(r["at"], r["price"]) for r in db.execute("SELECT at, price FROM observations WHERE key = ? ORDER BY at", (k,))]
    return out


def meta_get(db, k, default=None):
    r = db.execute("SELECT v FROM meta WHERE k = ?", (k,)).fetchone()
    return r["v"] if r else default


def meta_set(db, k, v):
    db.execute("INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v", (k, str(v)))
    db.commit()
