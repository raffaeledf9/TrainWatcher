"""One run of the GitHub job: handle queued commands, Check due Watches, send Alerts, publish the snapshots the
Worker uses for instant replies, save state.

Logs are public (ADR 0002): print counts and timings only, never routes, prices, ids or tokens.
Env: WORKER_URL, WORKER_SECRET, TELEGRAM_TOKEN, OWNER_CHAT_ID, STATE_DB (default state.db), GITHUB_EVENT_NAME (set by Actions).
"""
import base64
import json
import os
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone

from trainwatcher import alerts, check, i18n, model, render, schedule, stations, store

try:
    from zoneinfo import ZoneInfo
    ROME = ZoneInfo("Europe/Rome")
except Exception:  # no tz database: Italy's winter offset is close enough for display
    ROME = timezone(timedelta(hours=1))

GH_TOKEN_EXPIRES = date(2027, 10, 5)  # the Worker's fine-grained token; update when rotating it
TOKEN_URL = "https://github.com/settings/personal-access-tokens"
OP_NAME = {"T": "Trenitalia", "I": "Italo"}


# ---------- I/O ----------
def worker(path, body=None):
    req = urllib.request.Request(os.environ["WORKER_URL"].rstrip("/") + path, data=json.dumps(body or {}).encode(),
                                 headers={"Authorization": "Bearer " + os.environ["WORKER_SECRET"], "Content-Type": "application/json",
                                          "User-Agent": "trainwatcher-job"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def try_worker(path, body=None):
    """None when the Worker is unreachable: the job must still check prices and raise the dead-man notice."""
    try:
        return worker(path, body)
    except (OSError, ValueError) as e:
        print("worker unreachable", path, type(e).__name__)
        return None


def telegram(method, **params):
    req = urllib.request.Request(f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}/{method}",
                                 data=json.dumps(params).encode(), headers={"Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            with e:
                if e.code != 429 or attempt == 2:  # 429: too many messages at once; Telegram says how long to wait
                    print("telegram error", method, e.code)
                    return {}
                try:
                    wait = json.load(e).get("parameters", {}).get("retry_after", 5)
                except ValueError:
                    wait = 5
            time.sleep(min(wait, 30) + 1)
        except OSError as e:
            print("telegram error", method, type(e).__name__)
            return {}
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
        own = arg.isdigit() and (w := store.get_watch(db, int(arg))) is not None and w.user_id == uid and w.status == "active"
        if act == "nw" and own:
            forced.add(int(arg))
        elif act == "dy" and own:
            store.set_status(db, int(arg), "past")
        elif act == "wt":
            spec = store.meta_get(db, f"search:{arg}")
            if spec and json.loads(spec)["user_id"] == uid:
                db.execute("DELETE FROM meta WHERE k = ?", (f"search:{arg}",))  # a second tap creates nothing
                w = model.Watch.from_json(json.loads(spec))
                w.id = store.add_watch(db, w, now)
                forced.add(w.id)
                send(uid, (i18n.t(lang, "created", w=render.route(w, lang, short=True) + " · " + render.watch_days(w, lang)), []), local, silent=False)


# ---------- checks ----------
def to_local(utc_naive):
    return utc_naive.replace(tzinfo=timezone.utc).astimezone(ROME).replace(tzinfo=None)


def stale_since(db, watch):
    """While an operator of this watch is failing, status views say since when the prices are."""
    lasts = [store.meta_get(db, f"last_ok_at:{op}") for op in watch.operators if int(store.meta_get(db, f"fail_runs:{op}", "0"))]
    lasts = [x for x in lasts if x]
    return to_local(datetime.fromisoformat(min(lasts))) if lasts else None


def op_health(db, now, local, failing, tried):
    """Per operator searched this run: count consecutive failing runs, tell the Owner at 3 and on recovery."""
    owner, sent = int(os.environ["OWNER_CHAT_ID"]), 0
    for op in sorted(tried, reverse=True):
        before = int(store.meta_get(db, f"fail_runs:{op}", "0"))
        n = before + 1 if op in failing else 0
        key = "fail" if n == 3 else "op_ok" if n == 0 and before >= 3 else None
        if key:
            send(owner, (i18n.t(lang_of(db, owner), key, ops=OP_NAME[op]), []), local)
            sent += 1
        if n == 0:
            store.meta_set(db, f"last_ok_at:{op}", now.isoformat())
        store.meta_set(db, f"fail_runs:{op}", n)
    return sent


def unanswered(watch, statuses, today):
    """Operators whose search for this watch failed in this run (its status view says so instead of 'no trains')."""
    return sorted({OP_NAME[u[0]] for u in check.fetch_units(watch, today) if statuses.get(u) not in (None, "OK", "EMPTY", "NA")})


def check_and_alert(db, now, local, forced, searches):
    today = local.date()
    tomorrow = today + timedelta(days=1)
    due = store.due_watches(db, now)
    due_ids = {w.id for w in due}
    due += [store.get_watch(db, i) for i in forced if i not in due_ids and store.get_watch(db, i)]
    take, _, deferred = schedule.plan(due, now, today=today, first=forced)
    units = list(dict.fromkeys(u for w in take + searches for u in check.fetch_units(w, today)))
    t0 = time.time()
    statuses, failing = check.fetch_and_record(db, units, now) if units else ({}, set())
    fetch_s = time.time() - t0
    sent = op_health(db, now, local, failing, {u[0] for u, st in statuses.items() if st != check.NA})
    for w in take:
        lang = lang_of(db, w.user_id)
        vs, _, _, total = check.views(w, db, since=today)  # the status view still lists today's remaining trains
        events, state = [], getattr(w, "alert_state", {}) or {}
        if w.alertable(today):                             # alerts only for days that haven't started
            va, lowest, legs, _ = check.views(w, db, since=tomorrow)
            low_day = date.fromisoformat(va[0]["ties"][0].dep[:10]) if va[0]["ties"] else None
            events, state = alerts.evaluate(w, state, lowest, check.live_ok(w, statuses, tomorrow), today, legs=legs, low_day=low_day)
        if any(e["kind"] == "baseline" for e in events) or w.id in forced:
            send(w.user_id, render.status(w, vs, local, lang, today, total=total, stale_since=stale_since(db, w),
                                          failed=unanswered(w, statuses, today)), local, silent=False)
            sent += 1
        rest = [e for e in events if e["kind"] != "baseline"]
        if rest:
            send(w.user_id, render.alert(w, rest, va, local, lang, today), local)
            sent += 1
        store.mark_checked(db, w, now, schedule.next_check(w, now, today), state)
    for w in deferred:
        store.mark_skipped(db, w)
    for i, w in enumerate(searches):
        w.id = None
        sid = f"{int(now.timestamp())}{i}"
        store.meta_set(db, f"search:{sid}", json.dumps(w.to_json()))
        vs, lowest, legs, total = check.views(w, db, since=today)
        send(w.user_id, render.status(w, vs, local, lang_of(db, w.user_id), today, search=True, total=total, search_id=sid,
                                      stale_since=stale_since(db, w), failed=unanswered(w, statuses, today)), local, silent=False)
        sent += 1
    db.commit()
    bad = sum(1 for s in statuses.values() if s not in ("OK", "EMPTY", "NA"))
    return len(take), len(deferred), len(units), bad, sent, fetch_s, failing


# ---------- snapshots for the Worker's instant replies ----------
def snapshots(db, now, local):
    rows, users = [], {r["user_id"] for r in db.execute("SELECT DISTINCT user_id FROM watches")} | {int(os.environ["OWNER_CHAT_ID"])}
    for uid in users:
        lang = lang_of(db, uid)
        active = store.watches(db, uid)
        items = []
        for n, w in enumerate(active, 1):
            vs, lowest, legs, total = check.views(w, db, since=local.date())
            low_offer = vs[0]["ties"][0] if vs[0]["ties"] else None
            at = w.last_check.replace(tzinfo=timezone.utc).astimezone(ROME) if w.last_check else None
            items.append((w, low_offer if not w.round_trip else None, lowest, at))
            text, kb = render.status(w, vs, at or local, lang, local.date(), total=total, stale_since=stale_since(db, w))
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
        # 📈 for the most recent ones: their last chart stays in the Worker's snapshots after they end
        pkb = [[{"text": f"📈 {render.route(w, lang, short=True)} · {render.watch_days(w, lang)}", "callback_data": f"hi:{w.id}"}] for w in past[-10:]]
        rows.append({"key": f"{uid}:past", "user_id": uid, "body": json.dumps({"text": ptext, "kb": pkb})})
        rows.append({"key": f"{uid}:lang", "user_id": uid, "body": json.dumps(lang)})
    for lang in (i18n.EN, i18n.IT):
        rows.append({"key": f"strings:{lang}", "user_id": 0, "body": json.dumps({k: i18n.S[lang][k] for k in (
            "welcome", "lang_q", "lang_set", "ack", "deleted", "kept", "help", "list_empty", "past_empty", "del_yes", "del_no",
            "job_dead", "recovered", "dispatch_fail", "no_chart")})})
    rows.append({"key": "next_due", "user_id": 0, "body": json.dumps(next_due(db))})  # the Worker starts the next run then
    for i in range(0, len(rows), 50):
        worker("/job/snapshot", {"rows": rows[i:i + 50]})
    return len(rows)


# ---------- running unattended (map ticket 13) ----------
def next_due(db):
    """When the Worker should start the next run for a watch (epoch ms), None with no active watches."""
    m = db.execute("SELECT MIN(next_check) AS m FROM watches WHERE status = 'active'").fetchone()["m"]
    return int(datetime.fromisoformat(m).replace(tzinfo=timezone.utc).timestamp() * 1000) if m else None


def health(db, now, local, failing, checked, worker_ok, event, overdue=False):
    """Owner notices besides op_health: a dead Worker (the job's dead-man switch; the Worker watches the job), the
    silent Monday self-check, the GitHub token's expiry. overdue: a watch was due over 30 min before this run.
    Returns notices sent."""
    owner = int(os.environ["OWNER_CHAT_ID"])
    lang, notices = lang_of(db, owner), []
    get, put = (lambda k, d="0": store.meta_get(db, k, d)), (lambda k, v: store.meta_set(db, k, v))

    # The Worker starts a run whenever a watch is due, so the hourly fallback run finding one long overdue means it
    # didn't. Only a run the Worker started proves it works again (fallback runs alone would flap the notice).
    flagged = get("worker_dead", "") == "1"
    if not flagged and (not worker_ok or (event == "schedule" and overdue)):
        notices.append(i18n.t(lang, "worker_dead"))
        put("worker_dead", "1")
    elif flagged and worker_ok and event == "workflow_dispatch":
        notices.append(i18n.t(lang, "recovered"))
        put("worker_dead", "")

    put("week_checks", int(get("week_checks")) + checked)
    put("week_fails", int(get("week_fails")) + bool(failing))
    week = local.strftime("%G-W%V")
    if local.weekday() == 0 and local.hour >= 9 and get("weekly_sent", "") != week:
        active, fails = len(store.watches(db)), int(get("week_fails"))
        if active or fails:  # no watches and nothing broke: nothing worth a message
            send(owner, (i18n.t(lang, "weekly", w=active, c=get("week_checks"), f=fails), []), local, silent=True)
        put("weekly_sent", week)
        put("week_checks", 0)
        put("week_fails", 0)

    if local.date() >= GH_TOKEN_EXPIRES - timedelta(days=14) and get("token_reminded", "") != GH_TOKEN_EXPIRES.isoformat():
        notices.append(i18n.t(lang, "token", d=i18n.day(GH_TOKEN_EXPIRES, lang, year=True), url=TOKEN_URL))
        put("token_reminded", GH_TOKEN_EXPIRES.isoformat())
    for n in notices:
        send(owner, (n, []), local)
    return len(notices)


def count_unfinished(db, run_number):
    """GitHub numbers the runs. A gap since the last run that finished (and got its state saved) means runs that
    crashed or couldn't save; they can't count themselves, so the next run adds them to the week's failed runs."""
    last = int(store.meta_get(db, "last_ok_run", "0"))
    if run_number and last:
        store.meta_set(db, "week_fails", int(store.meta_get(db, "week_fails", "0")) + max(0, run_number - last - 1))


def main():
    t0 = time.time()
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    local = datetime.now(ROME).replace(tzinfo=None, microsecond=0)
    db = store.connect(os.environ.get("STATE_DB", "state.db"))
    runs = int(store.meta_get(db, "runs", "0")) + 1
    store.meta_set(db, "runs", runs)
    run_number = int(os.environ.get("GITHUB_RUN_NUMBER", "0"))
    count_unfinished(db, run_number)
    forced, searches, commands, worker_ok = set(), [], 0, True
    for _ in range(10):  # drain: commands may keep arriving while we work
        items = try_worker("/job/take")
        if items is None:
            worker_ok = False
        if not items:
            break
        for it in items:
            handle(db, it, now, local, forced, searches)
        commands += len(items)
        if try_worker("/job/ack", {"ids": [it["id"] for it in items]}) is None:
            worker_ok = False
            break
    for w in store.watches(db):
        if schedule.is_past(w, local.date()):
            store.set_status(db, w.id, "past")
    overdue = bool(store.due_watches(db, now - timedelta(minutes=30)))
    checked, deferred, units, bad, sent, fetch_s, failing = check_and_alert(db, now, local, forced, searches)
    notices = health(db, now, local, failing, checked, worker_ok, os.environ.get("GITHUB_EVENT_NAME", ""), overdue)
    store.prune(db, local.date())
    snaps = snapshots(db, now, local) if worker_ok else 0  # the workflow tells the Worker "done" once state is saved
    print(f"run #{runs}: commands={commands} watches_checked={checked} deferred={deferred} units={units} failed_units={bad} "
          f"failing_ops={len(failing)} worker_ok={worker_ok} messages={sent} notices={notices} snapshots={snaps} "
          f"fetch={fetch_s:.1f}s total={time.time() - t0:.1f}s")
    if run_number:
        store.meta_set(db, "last_ok_run", run_number)
    return 0


if __name__ == "__main__":
    sys.exit(main())
