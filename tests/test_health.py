"""Running unattended (map ticket 13): the canary tells 'no trains' from 'operator broken', the Owner hears about
3 failing runs and the recovery, a dead Worker, the Monday self-check and the token expiry."""
import os
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

from trainwatcher import check, run, store
from trainwatcher.offers import BROKEN, EMPTY, OK, Offer, SearchResult
from trainwatcher.operators import trenitalia

NOW = datetime(2026, 10, 6, 12, 0)
UNIT = ("T", "milano-tutte", "roma-termini", date(2026, 11, 10), "adult", None)
CANARY_IDS = (830001700, 830008409)
OFFER = Offer("trenitalia", "9607", "FR", "2026-11-11T07:00", "2026-11-11T10:00", "a", "b", "Standard", "Base", 99.0, None, False)


def fake_search(canary_ok):
    def search(a, b, day, *args, **kw):
        if (a, b) == CANARY_IDS:
            return SearchResult(OK, [OFFER]) if canary_ok else SearchResult(BROKEN)
        return SearchResult(EMPTY)
    return search


class Canary(unittest.TestCase):
    def test_empty_with_healthy_canary_is_really_empty(self):
        db = store.connect(":memory:")
        with mock.patch.object(trenitalia, "search", fake_search(True)):
            statuses, failing = check.fetch_and_record(db, [UNIT], NOW, session=object())
        self.assertEqual((statuses[UNIT], failing), (EMPTY, set()))

    def test_empty_with_failing_canary_is_broken_and_not_stored(self):
        db = store.connect(":memory:")
        store.record(db, UNIT, [OFFER], NOW - timedelta(hours=1))
        with mock.patch.object(trenitalia, "search", fake_search(False)):
            statuses, failing = check.fetch_and_record(db, [UNIT], NOW, session=object())
        self.assertEqual((statuses[UNIT], failing), (BROKEN, {"T"}))
        self.assertEqual(len(store.current_offers(db, UNIT)), 1)  # the last good prices stay


class Notices(unittest.TestCase):
    def setUp(self):
        self.db, self.sent = store.connect(":memory:"), []
        patches = [mock.patch.dict(os.environ, {"OWNER_CHAT_ID": "7"}),
                   mock.patch.object(run, "send", lambda uid, tk, local, silent=None: self.sent.append((tk[0], silent)))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def texts(self):
        return [t for t, _ in self.sent]

    def test_three_failing_runs_then_recovery(self):
        for _ in range(4):
            run.op_health(self.db, NOW, NOW, {"T"}, {"T"})
        run.op_health(self.db, NOW, NOW, set(), {"I"})  # a run without Trenitalia units doesn't reset it
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Trenitalia", self.sent[0][0])
        w = type("W", (), {"operators": ["T", "I"]})()
        self.assertEqual(run.stale_since(self.db, w), None)  # never worked yet: no "prices from"
        run.op_health(self.db, NOW, NOW, set(), {"T"})
        self.assertIn("working again", self.texts()[-1])
        run.op_health(self.db, NOW + timedelta(minutes=5), NOW, {"T"}, {"T"})
        self.assertEqual(run.stale_since(self.db, w), run.to_local(NOW))

    def test_dead_worker_once_then_recovered(self):
        h = lambda event, ok=True, now=NOW: run.health(self.db, now, NOW, set(), 0, ok, event)
        h("workflow_dispatch")
        h("schedule", ok=False)
        h("schedule", ok=False)
        h("workflow_dispatch")
        h("schedule", now=NOW + timedelta(hours=2))  # only hourly fallback runs: the Worker stopped dispatching
        self.assertEqual(len(self.sent), 3)
        self.assertIn("Cloudflare", self.sent[0][0])
        self.assertIn("working again", self.sent[1][0])
        self.assertIn("Cloudflare", self.sent[2][0])

    def test_monday_self_check_is_silent_and_weekly(self):
        monday = datetime(2026, 10, 12, 9, 5)
        run.health(self.db, NOW, datetime(2026, 10, 11, 20, 0), {"T"}, 4, True, "")
        run.health(self.db, NOW, monday, set(), 2, True, "")
        run.health(self.db, NOW, monday + timedelta(minutes=5), set(), 2, True, "")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][1], True)
        self.assertIn("6 checks, 1 failed", self.sent[0][0])

    def test_token_reminder_14_days_before_once(self):
        day = datetime.combine(run.GH_TOKEN_EXPIRES - timedelta(days=15), datetime.min.time()).replace(hour=12)
        tokens = []
        for d in (0, 1, 2):
            run.health(self.db, NOW, day + timedelta(days=d), set(), 0, True, "")
            tokens.append(sum("token" in t for t in self.texts()))
        self.assertEqual(tokens, [0, 1, 1])


if __name__ == "__main__":
    unittest.main()
