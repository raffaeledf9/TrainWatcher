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
FRIEND_MAX_WATCHES = 5  # the Owner has no cap
PAST_SHOWN = 40         # finished watches listed in /past


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


def chunks(text, kb, limit=3900):
    """Telegram rejects messages over 4096 characters: a longer text goes out as consecutive messages split at
    line breaks (lines carry whole HTML tags), the buttons on the last one. -> [(text, kb)]"""
    parts, cur, size = [], [], 0
    for line in text.split("\n"):
        line = line if len(line) <= limit else line[:limit - 1] + "…"
        if cur and size + len(line) + 1 > limit:
            parts.append("\n".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += len(line) + 1
    parts.append("\n".join(cur))
    return [(p, kb if i == len(parts) - 1 else []) for i, p in enumerate(parts)]


def send(user_id, text_kb, now_local, silent=None):
    for text, kb in chunks(*text_kb):
        telegram("sendMessage", chat_id=user_id, text=text, parse_mode="HTML", disable_web_page_preview=True,
                 disable_notification=alerts.silent(now_local) if silent is None else silent,
                 **({"reply_markup": {"inline_keyboard": kb}} if kb else {}))


def status_messages(db, w, at, lang, today, **kw):
    """A watch's or search's status view: one message, or one per month for a period spanning several."""
    months = check.split_months(w, today)
    total = check.views(w, db, since=today)[3]
    return [render.status(w, check.views(w, db, since=today, month=m)[0], at, lang, today, total=total if i == len(months) else None,
                          part=(i, len(months), m) if m else None, **kw) for i, m in enumerate(months, 1)]


def lang_of(db, uid):
    return store.user_lang(db, uid) or i18n.EN


# ---------- commands from the Worker queue ----------
def handle(db, item, now, local, forced, searches):
    uid, kind, p = item["user_id"], item["kind"], json.loads(item["payload"] or "{}")
    lang = lang_of(db, uid)
    if kind == "lang":
        if p.get("lang") in (i18n.EN, i18n.IT):  # an unknown language would break every message to this user
            store.set_user_lang(db, uid, p["lang"], now)
    elif kind == "form":
        try:
            w = model.from_payload(p.get("data"), uid, {e["k"] for e in stations.entries()}, local.date())
        except model.Invalid as e:
            send(uid, (i18n.t(lang, "invalid", why=i18n.reason(lang, str(e))), []), local, silent=False)
            return
        if p.get("mode") == "search":
            searches.append(w)
        elif not over_cap(db, uid, lang, local):
            w.id = store.add_watch(db, w, now)
            forced.add(w.id)
            if schedule.load(store.watches(db), local.date()) > schedule.WARN_LOAD:  # capacity is shared by everyone
                send(uid, (i18n.t(lang, "load_warn"), []), local, silent=False)
    elif kind == "revoke" and uid == int(os.environ["OWNER_CHAT_ID"]):  # the Owner removed a friend: stop their watches
        for w in store.watches(db, int(p["user_id"])):
            store.set_status(db, w.id, "past")
    elif kind == "callback":
        act, _, arg = p.get("data", "").partition(":")
        own = arg.isdigit() and (w := store.get_watch(db, int(arg))) is not None and w.user_id == uid and w.status == "active"
        if act == "nw" and own:
            forced.add(int(arg))
        elif act == "dy" and own:
            store.set_status(db, int(arg), "past")
        elif act == "wt":
            spec = store.meta_get(db, f"search:{arg}")
            if spec and json.loads(spec)["user_id"] == uid and not over_cap(db, uid, lang, local):
                db.execute("DELETE FROM meta WHERE k = ?", (f"search:{arg}",))  # a second tap creates nothing
                w = model.Watch.from_json(json.loads(spec))
                w.id = store.add_watch(db, w, now)
                forced.add(w.id)
                send(uid, (i18n.t(lang, "created", w=render.route(w, lang, short=True) + " · " + render.watch_days(w, lang)), []), local, silent=False)


def over_cap(db, uid, lang, local):
    """Friends have at most FRIEND_MAX_WATCHES active watches; tells them when a new one is refused."""
    if uid == int(os.environ["OWNER_CHAT_ID"]) or len(store.watches(db, uid)) < FRIEND_MAX_WATCHES:
        return False
    send(uid, (i18n.t(lang, "cap_reached", n=FRIEND_MAX_WATCHES), []), local, silent=False)
    return True


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
        try:
            sent += check_one(db, w, now, local, forced, statuses)
        except Exception as e:  # one failing watch must not stop everyone's alerts (it would fail every run)
            db.rollback()
            print("watch failed", type(e).__name__)
            store.mark_checked(db, w, now, schedule.next_check(w, now, today), getattr(w, "alert_state", {}) or {})
            db.commit()  # retried at its normal cadence, not at every run
    for w in deferred:
        store.mark_skipped(db, w)
    for i, w in enumerate(searches):
        try:
            w.id = None
            sid = f"{int(now.timestamp())}{i}"
            store.meta_set(db, f"search:{sid}", json.dumps(w.to_json()))
            for msg in status_messages(db, w, local, lang_of(db, w.user_id), today, search=True, search_id=sid,
                                       stale_since=stale_since(db, w), failed=unanswered(w, statuses, today)):
                send(w.user_id, msg, local, silent=False)
                sent += 1
        except Exception as e:
            print("search failed", type(e).__name__)
    db.commit()
    bad = sum(1 for s in statuses.values() if s not in ("OK", "EMPTY", "NA"))
    return len(take), len(deferred), len(units), bad, sent, fetch_s, failing


def check_one(db, w, now, local, forced, statuses):
    """Alerts and status of one checked watch, committed at once: alerts already sent are never sent again."""
    today = local.date()
    tomorrow = today + timedelta(days=1)
    lang = lang_of(db, w.user_id)
    events, state = [], getattr(w, "alert_state", {}) or {}
    if w.alertable(today):                             # alerts only for days that haven't started
        va, lowest, legs, _ = check.views(w, db, since=tomorrow)
        low_day = date.fromisoformat(va[0]["ties"][0].dep[:10]) if va[0]["ties"] else None
        events, state = alerts.evaluate(w, state, lowest, check.live_ok(w, statuses, tomorrow), today, legs=legs, low_day=low_day)
    msgs = []                                          # everything rendered before anything is sent
    if any(e["kind"] == "baseline" for e in events) or w.id in forced:
        msgs += [(m, False) for m in status_messages(db, w, local, lang, today, stale_since=stale_since(db, w),
                                                     failed=unanswered(w, statuses, today))]  # from today on: today's remaining trains
    rest = [e for e in events if e["kind"] != "baseline"]
    if rest:
        msgs.append((render.alert(w, rest, va, local, lang, today), None))
    for m, silent in msgs:
        send(w.user_id, m, local, silent=silent)
    store.mark_checked(db, w, now, schedule.next_check(w, now, today), state)
    db.commit()
    return len(msgs)


# ---------- snapshots for the Worker's instant replies ----------
def watch_snapshots(db, uid, n, w, lang, local, items):
    """Status (one part per month), delete question and chart of watch number n; adds its line to items."""
    vs, lowest, legs, total = check.views(w, db, since=local.date())
    low_offer = vs[0]["ties"][0] if vs[0]["ties"] else None
    at = w.last_check.replace(tzinfo=timezone.utc).astimezone(ROME) if w.last_check else None
    parts = [c for msg in status_messages(db, w, at or local, lang, local.date(), stale_since=stale_since(db, w)) for c in chunks(*msg)]
    q = render.delete_question(n, w, lang)
    rows = [{"key": f"{uid}:status:{w.id}", "user_id": uid, "body": json.dumps({"parts": [{"text": t, "kb": k} for t, k in parts]})},
            {"key": f"{uid}:delete:{w.id}", "user_id": uid, "body": json.dumps({"text": q[0], "kb": q[1], "n": n})}]
    series = check.chart_series(w, db, vs)
    if series:
        try:
            from trainwatcher.charts import chart_png
            png = chart_png(series, render.route(w, lang, short=True) + " · " + render.watch_days(w, lang, local.date()), lang, w.max_price)
            rows.append({"key": f"{uid}:chart:{w.id}", "user_id": uid, "body": json.dumps({"png": base64.b64encode(png).decode()})})
        except ImportError:
            pass
    items.append((w, low_offer if not w.round_trip else None, lowest, at))
    return rows


def snapshots(db, now, local):
    rows, users = [], {r["user_id"] for r in db.execute("SELECT DISTINCT user_id FROM watches")} | {int(os.environ["OWNER_CHAT_ID"])}
    for uid in users:
        lang = lang_of(db, uid)
        active = store.watches(db, uid)
        items = []
        for n, w in enumerate(active, 1):
            try:
                rows += watch_snapshots(db, uid, n, w, lang, local, items)
            except Exception as e:  # one failing watch must not leave everyone without instant replies
                print("snapshot failed", type(e).__name__)
        rows.append({"key": f"{uid}:list", "user_id": uid, "body": json.dumps(
            {"parts": [{"text": t, "kb": k} for t, k in render.watch_list_parts(items, lang, local.date())]})})
        past = store.watches(db, uid, status="past")
        # the latest PAST_SHOWN only: months of use would exceed Telegram's 4096 characters (73 long lines fit)
        older = [i18n.t(lang, "more_past", n=len(past) - PAST_SHOWN)] if len(past) > PAST_SHOWN else []
        ptext = (f"🗂 <b>{i18n.t(lang, 'past_t')}</b>\n\n" + "\n".join(older + [f"• {render.route(w, lang, short=True)} · {render.watch_days(w, lang)}"
                                                                         for w in past[-PAST_SHOWN:]])) if past else i18n.t(lang, "past_empty")
        # 📈 for the most recent ones: their last chart stays in the Worker's snapshots after they end
        pkb = [[{"text": f"📈 {render.route(w, lang, short=True)} · {render.watch_days(w, lang)}", "callback_data": f"hi:{w.id}"}] for w in past[-10:]]
        rows.append({"key": f"{uid}:past", "user_id": uid, "body": json.dumps({"text": ptext, "kb": pkb})})
        # not {uid}:lang: the Worker owns it (it writes it the moment a language is chosen); republishing it here
        # from this run's possibly older copy would undo a choice made while the run was working
    for lang in (i18n.EN, i18n.IT):
        rows.append({"key": f"strings:{lang}", "user_id": 0, "body": json.dumps({k: i18n.S[lang][k] for k in (
            "welcome", "lang_q", "lang_set", "ack", "deleted", "kept", "help", "list_empty", "past_empty",
            "job_dead", "recovered", "dispatch_fail", "no_chart", "req_sent", "req_wait", "req_new", "btn_allow", "btn_deny",
            "btn_remove", "btn_readd", "allowed_owner", "denied_owner", "removed_owner", "allowed_friend", "removed_friend",
            "invite_text", "invite_reset", "friends_t", "friends_empty", "st_pending", "st_allowed", "st_denied", "st_removed",
            "help_owner", "rate_limited", "try_again")})})
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

    active = store.watches(db)
    load = schedule.load(active, local.date())
    if load > schedule.WARN_LOAD and get("load_warned", "") != "1":  # once per crossing; re-armed below 60 %
        notices.append(i18n.t(lang, "load_owner", p=round(load * 100), n=len(active)))
        put("load_warned", "1")
    elif load < 0.6:
        put("load_warned", "")

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
            try:
                handle(db, it, now, local, forced, searches)
            except Exception as e:  # one bad command must not crash every run: it would be offered again and again
                print("command failed", it.get("kind"), type(e).__name__)
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
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
