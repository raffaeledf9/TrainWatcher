"""One run of the GitHub job: handle queued commands, Check due Watches, send Alerts, publish the snapshots the
Worker uses for instant replies, save state.

Logs are public (ADR 0002): print counts and timings only, never routes, prices, ids or tokens.
Env: WORKER_URL, WORKER_SECRET, TELEGRAM_TOKEN, OWNER_CHAT_ID, STATE_DB (default state.db).
"""
import base64
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

from trainwatcher import alerts, check, i18n, model, render, schedule, stations, store

try:
    from zoneinfo import ZoneInfo
    ROME = ZoneInfo("Europe/Rome")
except Exception:  # no tz database: Italy's winter offset is close enough for display
    ROME = timezone(timedelta(hours=1))


# ---------- I/O ----------
def worker(path, body=None):
    req = urllib.request.Request(os.environ["WORKER_URL"].rstrip("/") + path, data=json.dumps(body or {}).encode(),
                                 headers={"Authorization": "Bearer " + os.environ["WORKER_SECRET"], "Content-Type": "application/json",
                                          "User-Agent": "trainwatcher-job"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def telegram(method, **params):
    req = urllib.request.Request(f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}/{method}",
                                 data=json.dumps(params).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        print("telegram error", method, e.code)
        return {}


def send(user_id, text_kb, now_local, silent=None):
    text, kb = text_kb
    telegram("sendMessage", chat_id=user_id, text=text, parse_mode="HTML", disable_web_page_preview=True,
             disable_notification=alerts.silent(now_local) if silent is None else silent,
             **({"reply_markup": {"inline_keyboard": kb}} if kb else {}))


def lang_of(db, uid):
    return store.user_lang(db, uid) or i18n.EN


# ---------- commands from the Worker queue ----------
def handle(db, item, now, local, forced, searches):
    uid, kind, p = item["user_id"], item["kind"], json.loads(item["payload"] or "{}")
    lang = lang_of(db, uid)
    if kind == "lang":
        store.set_user_lang(db, uid, p["lang"], now)
    elif kind == "form":
        try:
            w = model.from_payload(p.get("data"), uid, {e["k"] for e in stations.entries()}, local.date())
        except model.Invalid as e:
            send(uid, (i18n.t(lang, "invalid", why=str(e)), []), local, silent=False)
            return
        if p.get("mode") == "search":
            searches.append(w)
        else:
            w.id = store.add_watch(db, w, now)
            forced.add(w.id)
            if schedule.load(store.watches(db, uid), local.date()) > schedule.WARN_LOAD:
                send(uid, (i18n.t(lang, "load_warn"), []), local, silent=False)
    elif kind == "callback":
        act, _, arg = p.get("data", "").partition(":")
        if act == "nw" and arg.isdigit():
            forced.add(int(arg))
        elif act == "dy" and arg.isdigit():
            store.set_status(db, int(arg), "past")
        elif act == "wt":
            spec = store.meta_get(db, f"search:{arg}")
            if spec:
                w = model.Watch.from_json(json.loads(spec))
                w.id = store.add_watch(db, w, now)
                forced.add(w.id)
                send(uid, (i18n.t(lang, "created", w=render.route(w, lang, short=True) + " · " + render.watch_days(w, lang)), []), local, silent=False)


# ---------- checks ----------
def check_and_alert(db, now, local, forced, searches):
    due = store.due_watches(db, now)
    due_ids = {w.id for w in due}
    due += [store.get_watch(db, i) for i in forced if i not in due_ids and store.get_watch(db, i)]
    take, _, deferred = schedule.plan(due, now)
    units = list(dict.fromkeys(u for w in take + searches for u in check.fetch_units(w)))
    t0 = time.time()
    statuses = check.fetch_and_record(db, units, now) if units else {}
    fetch_s = time.time() - t0
    sent = 0
    for w in take:
        lang = lang_of(db, w.user_id)
        vs, lowest, legs, total = check.views(w, db)
        ok = check.live_ok(w, statuses)
        events, state = alerts.evaluate(w, getattr(w, "alert_state", {}) or {}, lowest, ok, local.date(), legs=legs)
        if any(e["kind"] == "baseline" for e in events) or w.id in forced:
            send(w.user_id, render.status(w, vs, local, lang, local.date(), total=total), local, silent=False)
            sent += 1
        rest = [e for e in events if e["kind"] != "baseline"]
        if rest:
            send(w.user_id, render.alert(w, rest, vs, local, lang, local.date()), local)
            sent += 1
        store.mark_checked(db, w, now, schedule.next_check(w, now), state)
    for w in deferred:
        store.mark_skipped(db, w)
    for i, w in enumerate(searches):
        w.id = None
        sid = f"{int(now.timestamp())}{i}"
        store.meta_set(db, f"search:{sid}", json.dumps(w.to_json()))
        vs, lowest, legs, total = check.views(w, db)
        send(w.user_id, render.status(w, vs, local, lang_of(db, w.user_id), local.date(), search=True, total=total, search_id=sid), local, silent=False)
        sent += 1
    db.commit()
    bad = sum(1 for s in statuses.values() if s not in ("OK", "EMPTY", "NA"))
    return len(take), len(deferred), len(units), bad, sent, fetch_s


# ---------- snapshots for the Worker's instant replies ----------
def snapshots(db, now, local):
    rows, users = [], {r["user_id"] for r in db.execute("SELECT DISTINCT user_id FROM watches")} | {int(os.environ["OWNER_CHAT_ID"])}
    for uid in users:
        lang = lang_of(db, uid)
        active = store.watches(db, uid)
        items = []
        for n, w in enumerate(active, 1):
            vs, lowest, legs, total = check.views(w, db)
            low_offer = vs[0]["ties"][0] if vs[0]["ties"] else None
            at = w.last_check.replace(tzinfo=timezone.utc).astimezone(ROME) if w.last_check else None
            items.append((w, low_offer if not w.round_trip else None, lowest, at))
            text, kb = render.status(w, vs, at or local, lang, local.date(), total=total)
            rows.append({"key": f"{uid}:status:{w.id}", "user_id": uid, "body": json.dumps({"text": text, "kb": kb})})
            q = render.delete_question(n, w, lang)
            rows.append({"key": f"{uid}:delete:{w.id}", "user_id": uid, "body": json.dumps({"text": q[0], "kb": q[1], "n": n})})
            series = check.chart_series(w, db, vs)
            if series:
                try:
                    from trainwatcher.charts import chart_png
                    png = chart_png(series, render.route(w, lang, short=True) + " · " + render.watch_days(w, lang, local.date()), lang, w.max_price)
                    rows.append({"key": f"{uid}:chart:{w.id}", "user_id": uid, "body": json.dumps({"png": base64.b64encode(png).decode()})})
                except ImportError:
                    pass
        text, kb = render.watch_list(items, lang, local.date())
        rows.append({"key": f"{uid}:list", "user_id": uid, "body": json.dumps({"text": text, "kb": kb})})
        past = store.watches(db, uid, status="past")
        ptext = f"🗂 <b>{i18n.t(lang, 'past_t')}</b>\n\n" + "\n".join(f"• {render.route(w, lang, short=True)} · {render.watch_days(w, lang)}" for w in past) if past else i18n.t(lang, "past_empty")
        rows.append({"key": f"{uid}:past", "user_id": uid, "body": json.dumps({"text": ptext, "kb": []})})
        rows.append({"key": f"{uid}:lang", "user_id": uid, "body": json.dumps(lang)})
    for lang in (i18n.EN, i18n.IT):
        rows.append({"key": f"strings:{lang}", "user_id": 0, "body": json.dumps({k: i18n.S[lang][k] for k in (
            "welcome", "lang_q", "lang_set", "ack", "deleted", "kept", "help", "list_empty", "past_empty", "del_yes", "del_no")})})
    for i in range(0, len(rows), 50):
        worker("/job/snapshot", {"rows": rows[i:i + 50]})
    return len(rows)


def main():
    t0 = time.time()
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    local = datetime.now(ROME).replace(tzinfo=None, microsecond=0)
    db = store.connect(os.environ.get("STATE_DB", "state.db"))
    runs = int(store.meta_get(db, "runs", "0")) + 1
    store.meta_set(db, "runs", runs)
    forced, searches, commands = set(), [], 0
    for _ in range(10):  # drain: commands may keep arriving while we work
        items = worker("/job/take")
        if not items:
            break
        for it in items:
            handle(db, it, now, local, forced, searches)
        commands += len(items)
        worker("/job/ack", {"ids": [it["id"] for it in items]})
    for w in store.watches(db):
        if schedule.is_past(w, local.date()):
            store.set_status(db, w.id, "past")
    checked, deferred, units, bad, sent, fetch_s = check_and_alert(db, now, local, forced, searches)
    snaps = snapshots(db, now, local)
    worker("/job/done", {"bad_units": bad})
    print(f"run #{runs}: commands={commands} watches_checked={checked} deferred={deferred} units={units} failed_units={bad} "
          f"messages={sent} snapshots={snaps} fetch={fetch_s:.1f}s total={time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
