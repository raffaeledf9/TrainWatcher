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

from trainwatcher import alerts, check, model, prices, run, schedule, store
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
        p = mock.patch.object(run, "send")
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

    def test_three_cases(self):
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
        for case, ls, fetch in (("present", 0, 0), ("absent", 2, 128), ("down", 128, 128)):
            with tempfile.TemporaryDirectory() as d:
                Path(d, "git").write_text(f'#!/bin/bash\ncase "$1" in ls-remote) exit {ls};; fetch) exit {fetch};; show) echo x;; esac\n')
                Path(d, "openssl").write_text("#!/bin/bash\ntouch state.db\n")
                Path(d, "sleep").write_text("#!/bin/bash\n")
                for f in ("git", "openssl", "sleep"):
                    os.chmod(Path(d, f), 0o755)
                r = subprocess.run(["bash", "-e", "-c", script], cwd=d, env={**os.environ, "PATH": d + ":" + os.environ["PATH"]},
                                   capture_output=True, text=True)
                out[case] = (r.returncode, r.stdout.strip().splitlines()[-1])
        self.assertEqual(out, {"present": (0, "state restored"), "absent": (0, "no state yet"), "down": (1, "state branch unreachable")})


if __name__ == "__main__":
    unittest.main()
