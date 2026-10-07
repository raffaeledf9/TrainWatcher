"""Bugs found in the 2026-10-07 review, each pinned by the concrete case that exposed it."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import urllib.error
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from unittest import mock

from trainwatcher import alerts, check, model, prices, render, run, schedule, store
from trainwatcher.charts import _merge
from trainwatcher.offers import ITALO, TRENITALIA, Offer

ROOT = Path(__file__).resolve().parents[1]
KEYS = {"milano-tutte", "roma-termini"}
NOW = datetime(2026, 10, 6, 12, 0)


def watch(today=date(2026, 10, 6), **kw):
    p = {"v": 1, "from": "milano-tutte", "to": "roma-termini", "out": {"d": ["2026-11-10"], "w": None}}
    p.update(kw)
    return model.from_payload(p, 7, KEYS, today)


def offer(op, day, dep, price, ar=False):
    return Offer(op, "1", "FR", f"2026-11-{day}T{dep}", f"2026-11-{day}T{dep[:2]}:59", "a", "b", "Standard", "Base", price, None, ar)


class PurchaseWindowPerDay(unittest.TestCase):
    """A multi-day FrecciaYOUNG watch: the window is per travel day, not the watch's first day."""
    w = watch(out={"d": ["2026-11-10", "2026-11-30"], "w": None}, pax="young", fares=["T:FrecciaYOUNG"])
    state = {"low": 30.0, "legs": None}

    def kinds(self, today, low_day):
        return [e["kind"] for e in alerts.evaluate(self.w, self.state, 30.0, True, today, low_day=low_day)[0]]

    def test_no_false_still_on_sale_while_the_cheapest_day_is_open(self):
        self.assertEqual(self.kinds(date(2026, 11, 1), date(2026, 11, 20)), [])

    def test_still_on_sale_for_the_cheapest_day_past_its_window(self):
        self.assertEqual(self.kinds(date(2026, 11, 1), date(2026, 11, 10)), ["still_on_sale"])

    def test_last_day_reminder_for_the_cheapest_day(self):
        self.assertEqual(self.kinds(date(2026, 11, 9), date(2026, 11, 20)), ["last_day"])

    def test_gone_by_window_only_once_the_last_day_closed(self):
        e = alerts.evaluate(self.w, self.state, None, True, date(2026, 11, 1))[0]
        self.assertEqual(e[0]["reason"], "sold_out")  # Nov 12-30 were still on sale: it sold out
        e = alerts.evaluate(self.w, self.state, None, True, date(2026, 11, 25))[0]
        self.assertEqual(e[0]["reason"], "window")


class RoundTripPairs(unittest.TestCase):
    def test_return_must_leave_after_the_outbound(self):
        outs = [offer(TRENITALIA, 10, "08:00", 50), offer(TRENITALIA, 12, "08:00", 20)]
        rets = [offer(TRENITALIA, 11, "18:00", 15), offer(TRENITALIA, 14, "18:00", 40)]
        total, a, b = prices.round_trip_total(outs, rets)
        self.assertEqual((total, a.dep[8:10], b.dep[8:10]), (60, "12", "14"))

    def test_ar_fares_pair_within_one_operator(self):
        outs = [offer(TRENITALIA, 10, "08:00", 20, True), offer(ITALO, 10, "09:00", 30, True)]
        rets = [offer(TRENITALIA, 10, "18:00", 25, True), offer(ITALO, 10, "19:00", 10, True)]
        self.assertEqual(prices.round_trip_total(outs, rets)[0], 40)


class PastAndTravelDays(unittest.TestCase):
    def test_month_watch_skips_past_days_and_keeps_its_cadence(self):
        w = watch(out={"d": ["2026-10"], "w": None})
        today = date(2026, 10, 25)
        self.assertEqual(min(u[3] for u in check.fetch_units(w, today)), today)
        self.assertEqual(len(check.fetch_units(w, today)), 2 * 7)       # Oct 25-31, two operators
        self.assertEqual(schedule.cadence_minutes(w.next_day(today), today), 15)
        w = watch(out={"d": ["2026-11-01", "2026-11-30"], "w": None})
        self.assertEqual(schedule.cadence_minutes(w.next_day(date(2026, 11, 20)), date(2026, 11, 20)), 15)
        self.assertEqual(schedule.cadence_minutes(w.next_day(date(2026, 10, 7)), date(2026, 10, 7)), 120)

    def test_no_alerts_once_the_travel_day_began(self):
        w = watch()
        self.assertTrue(w.alertable(date(2026, 11, 9)))
        self.assertFalse(w.alertable(date(2026, 11, 10)))  # departures would look like rises, then "sold out"
        rt = watch(ret={"d": ["2026-11-12"], "w": None})
        self.assertFalse(rt.alertable(date(2026, 11, 10)))  # the trip is under way


class TelegramSlowDown(unittest.TestCase):
    def test_429_waits_and_retries(self):
        busy = urllib.error.HTTPError("u", 429, "Too Many", {}, BytesIO(json.dumps({"parameters": {"retry_after": 1}}).encode()))
        ok = mock.MagicMock()
        ok.__enter__.return_value = BytesIO(b'{"ok": true}')
        with mock.patch.dict(os.environ, {"TELEGRAM_TOKEN": "x"}), mock.patch.object(run.time, "sleep") as sleep, \
                mock.patch.object(run.urllib.request, "urlopen", side_effect=[busy, ok]):
            self.assertEqual(run.telegram("sendMessage", chat_id=1, text="hi"), {"ok": True})
        sleep.assert_called_once()


class Commands(unittest.TestCase):
    def setUp(self):
        self.db = store.connect(":memory:")
        self.addCleanup(self.db.close)
        for p in (mock.patch.object(run, "send"), mock.patch.dict(os.environ, {"OWNER_CHAT_ID": "7"})):
            p.start()
            self.addCleanup(p.stop)

    def handle(self, uid, data):
        run.handle(self.db, {"user_id": uid, "kind": "callback", "payload": json.dumps({"data": data})}, NOW, NOW, set(), [])

    def test_delete_only_your_own_watch(self):
        wid = store.add_watch(self.db, watch(), NOW)  # user 7
        self.handle(8, f"dy:{wid}")
        self.assertEqual(store.get_watch(self.db, wid).status, "active")
        self.handle(7, f"dy:{wid}")
        self.assertEqual(store.get_watch(self.db, wid).status, "past")

    def test_watch_this_twice_creates_one_watch(self):
        store.meta_set(self.db, "search:123", json.dumps(watch().to_json()))
        self.handle(7, "wt:123")
        self.handle(7, "wt:123")
        self.assertEqual(len(store.watches(self.db)), 1)


class Validation(unittest.TestCase):
    def test_fares_that_could_never_match_are_refused(self):
        with self.assertRaises(model.Invalid):
            watch(fares=["TI:A/R same day"])                 # one way
        with self.assertRaises(model.Invalid):
            watch(fares=["I:Low Cost"], ops=["T"])           # Italo fare, Trenitalia only
        watch(fares=["TI:A/R same day"], ret={"d": ["2026-11-10"], "w": None})


class FrecciabiancaClasses(unittest.TestCase):
    def test_standard_is_2nd_class_business_is_1st(self):
        fb = lambda cls: Offer(TRENITALIA, "8619", "FB", "2026-11-10T13:10", "2026-11-10T20:03", "a", "b", cls, "Base", 23.9)
        std, bus = watch(cls=["Standard"]), watch(cls=["Business"])
        leg = std.legs[0]
        self.assertEqual([prices.matches(fb("2ª Classe"), w, leg) for w in (std, bus)], [True, False])
        self.assertEqual([prices.matches(fb("1ª Classe"), w, leg) for w in (std, bus)], [False, True])


class SecondPass(unittest.TestCase):
    def test_one_train_is_singular(self):
        o = Offer(ITALO, "9967", "IT", "2026-11-10T06:15", "2026-11-10T09:24", "MC_", "RMT", "Smart", "Economy", 29.9)
        text, _ = render.status(watch(), [{"low": 29.9, "ties": [o], "others": []}], NOW, "en", date(2026, 10, 6))
        self.assertIn("€29.90</b> lowest · 1 train\n", text)

    def test_same_day_return_ticket_opens_the_return_search(self):
        w = watch(ret={"d": ["2026-11-10"], "w": None}, ops=["I"])
        out = Offer(ITALO, "9967", "IT", "2026-11-10T06:15", "2026-11-10T09:24", "MC_", "RMT", "Smart", "Economy", 29.9)
        back = Offer(ITALO, "9990", "IT", "2026-11-10T18:00", "2026-11-10T21:10", "RMT", "MC_", "Smart", "Economy", 19.9)
        views = [{"ties": [out], "pair": out}, {"ties": [back], "pair": back}]
        e = {"kind": "drop", "low": 49.8, "prev": 59.8, "legs": {"out": 29.9, "ret": 19.9}, "prev_legs": {"out": 29.9, "ret": 29.9}}
        _, kb = render.alert(w, [e], views, NOW, "en", date(2026, 10, 6))
        urls = {b["text"]: b.get("url", "") for b in kb[0]}
        self.assertIn("osc=MI0&dsc=RMT", urls["🎫 06:15 IT 9967"])
        self.assertIn("osc=RMT&dsc=MI0", urls["🎫 18:00 IT 9990"])

    def test_first_time_on_sale_is_not_back(self):
        w = watch()
        _, s = alerts.evaluate(w, {}, None, True, date(2026, 10, 6))          # created before sales opened
        self.assertEqual([e["kind"] for e in alerts.evaluate(w, s, 29.9, True, date(2026, 10, 7))[0]], ["on_sale"])
        _, s = alerts.evaluate(w, {}, 29.9, True, date(2026, 10, 6))
        _, s = alerts.evaluate(w, s, None, True, date(2026, 10, 7))           # sold out
        self.assertEqual([e["kind"] for e in alerts.evaluate(w, s, 29.9, True, date(2026, 10, 8))[0]], ["back"])


class StateSize(unittest.TestCase):
    """History of one single-day watch was 95 % seat-count changes: 1.9 MB after 11 checks."""

    def test_seat_changes_are_not_history(self):
        db = store.connect(":memory:")
        self.addCleanup(db.close)
        unit = ("I", "milano-tutte", "roma-termini", date(2026, 11, 10), "adult", None)
        o = lambda price, seats: Offer(ITALO, "9967", "IT", "2026-11-10T06:15", "2026-11-10T09:24", "MC_", "RMT", "Smart", "Economy", price, seats)
        for price, seats in ((29.9, 8), (29.9, 7), (29.9, 6), (34.9, 6)):
            store.record(db, unit, [o(price, seats)], NOW)
        self.assertEqual([p for _, p in store.history(db, [store.offer_key(unit, o(0, 0))])[store.offer_key(unit, o(0, 0))]], [29.9, 34.9])
        self.assertEqual(store.current_offers(db, unit)[0]["seats"], 6)  # seats left still shown

    def test_past_days_are_pruned(self):
        db = store.connect(":memory:")
        self.addCleanup(db.close)
        o = Offer(ITALO, "9967", "IT", "2026-11-10T06:15", "2026-11-10T09:24", "MC_", "RMT", "Smart", "Economy", 29.9)
        for day in (date(2026, 11, 9), date(2026, 11, 10)):
            store.record(db, ("I", "a", "b", day, "adult", None), [o], NOW)
        self.assertEqual(store.prune(db, date(2026, 11, 10)), 1)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM offers_last").fetchone()[0], 1)

    def test_what_the_owner_waits_for_is_never_postponed(self):
        due = [watch(), watch(out={"d": ["2026-11"], "w": None})]
        due[0].id, due[1].id = 1, 2
        take, _, deferred = schedule.plan(due, NOW, budget_s=1, first={2})
        self.assertEqual(([w.id for w in take], [w.id for w in deferred]), ([2], [1]))


class Charts(unittest.TestCase):
    def test_seat_changes_dont_split_equal_lines(self):
        a = [("2026-10-01T10:00", 29.9)]
        b = [("2026-10-01T10:00", 29.9), ("2026-10-01T12:00", 29.9)]  # 8 seats left -> 7: same price
        self.assertEqual(list(_merge([("A", a), ("B", b)]).values()), [["A", "B"]])


@unittest.skipUnless(shutil.which("node"), "node not installed")
class WorkerScheduling(unittest.TestCase):
    def test_decide(self):
        m, h = 60_000, 3_600_000
        cases = {
            "command": [{"last_run_ok": "0"}, 1, None, 7, 1 * m],
            "idle": [{"last_run_ok": "0"}, 0, None, 10, 1 * h],
            "due": [{"last_run_ok": "0"}, 0, 2 * m, 10, 5 * m],
            "due_off_slot": [{"last_run_ok": "0"}, 0, 2 * m, 7, 5 * m],
            "heartbeat": [{"last_run_ok": "0"}, 0, None, 10, 3 * h],
            "failing_waits": [{"last_run_ok": "0", "pending_since": str(1 * h)}, 1, None, 10, 1 * h + 20 * m],
            "failing_retries": [{"last_run_ok": "0", "pending_since": str(1 * h)}, 1, None, 30, 1 * h + 20 * m],
        }
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "w.mjs").write_text((ROOT / "worker" / "src" / "index.js").read_text(encoding="utf-8") + "\nexport { decide };\n", encoding="utf-8")
            (Path(d) / "t.mjs").write_text(
                "const { decide } = await import('./w.mjs');\n"
                f"const c = {json.dumps(cases)}, out = {{}};\n"
                "for (const k in c) out[k] = decide(...c[k]);\n"
                "console.log(JSON.stringify(out));\n", encoding="utf-8")
            out = json.loads(subprocess.run(["node", "t.mjs"], cwd=d, capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out, {"command": "queue", "idle": None, "due": "due", "due_off_slot": None, "heartbeat": "heartbeat",
                               "failing_waits": None, "failing_retries": "queue"})


@unittest.skipIf(os.name == "nt" or not shutil.which("bash"), "needs a POSIX shell (runs in CI)")
class RestoreState(unittest.TestCase):
    """An unreachable state branch must fail the step (so the empty state is never saved over the real one)."""

    def test_cases(self):
        lines = (ROOT / ".github" / "workflows" / "run.yml").read_text().splitlines()
        start = next(i for i, l in enumerate(lines) if l.strip() == "id: restore")
        start = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |") + 1
        indent = len(lines[start]) - len(lines[start].lstrip())
        body = []
        for l in lines[start:]:
            if l.strip() and len(l) - len(l.lstrip()) < indent:
                break
            body.append(l[indent:])
        script = "\n".join(body)
        out = {}
        # (ls-remote exit, fetch exit, file stored on the branch)
        for case, ls, fetch, stored in (("gzip", 0, 0, "state.db.gz.enc"), ("older format", 0, 0, "state.db.enc"),
                                        ("absent", 2, 128, None), ("down", 128, 128, None)):
            with tempfile.TemporaryDirectory() as d:
                Path(d, "git").write_text(f'#!/bin/bash\ncase "$1" in ls-remote) exit {ls};; fetch) exit {fetch};;\n'
                                          f'show) [ "$2" = "FETCH_HEAD:{stored}" ] && echo x || exit 128;; esac\n')
                Path(d, "openssl").write_text('#!/bin/bash\nwhile [ $# -gt 0 ]; do [ "$1" = -out ] && echo db > "$2"; shift; done\n')
                Path(d, "gunzip").write_text("#!/bin/bash\necho db\n")
                Path(d, "sleep").write_text("#!/bin/bash\n")
                for f in ("git", "openssl", "gunzip", "sleep"):
                    os.chmod(Path(d, f), 0o755)
                r = subprocess.run(["bash", "-e", "-c", script], cwd=d, env={**os.environ, "PATH": d + ":" + os.environ["PATH"]},
                                   capture_output=True, text=True)
                out[case] = (r.returncode, r.stdout.strip().splitlines()[-1], Path(d, "state.db").exists())
        self.assertEqual(out, {"gzip": (0, "state restored", True), "older format": (0, "state restored", True),
                               "absent": (0, "no state yet", False), "down": (1, "state branch unreachable", False)})


if __name__ == "__main__":
    unittest.main()
