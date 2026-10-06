"""check.py: which units a Watch needs, how stored offers become a view, and which Checks count as live."""
import unittest
from datetime import date, datetime

from trainwatcher import check, model, store
from trainwatcher.offers import ITALO, TRENITALIA, Offer

TODAY = date(2026, 10, 6)
KEYS = {"milano-tutte", "roma-termini"}
NOW = datetime(2026, 10, 6, 12, 0)


def watch(**kw):
    p = {"v": 1, "from": "milano-tutte", "to": "roma-termini", "out": {"d": ["2026-11-10"], "w": None}, "pax": "young", "ops": ["T", "I"]}
    p.update(kw)
    return model.from_payload(p, 1, KEYS, TODAY)


def o(price, dep, op=TRENITALIA, n="9607", fare="Super Economy", ar=False, day="2026-11-10"):
    return Offer(op, n, "FR" if op == TRENITALIA else "IT", f"{day}T{dep}", f"{day}T{dep[:2]}:59", "a", "b", "Standard" if op == TRENITALIA else "Smart", fare, price, None, ar)


class Units(unittest.TestCase):
    def test_trenitalia_shared_across_passengers_italo_not(self):
        us = check.fetch_units(watch())
        self.assertIn(("T", "milano-tutte", "roma-termini", date(2026, 11, 10), "adult", None), us)
        self.assertIn(("I", "milano-tutte", "roma-termini", date(2026, 11, 10), "young", None), us)

    def test_same_day_round_trip_is_one_fetch_two_record_units(self):
        w = watch(ret={"d": ["2026-11-10"], "w": None})
        self.assertEqual(len(check.fetch_units(w)), 2)
        self.assertEqual(check.record_units(w, 1)[0][1:3], ("roma-termini", "milano-tutte"))


class Views(unittest.TestCase):
    def test_views_and_live_ok(self):
        db, w = store.connect(":memory:"), watch(fares=["T:FrecciaYOUNG"])
        ut, ui = check.fetch_units(w)
        store.record(db, ut, [o(39, "07:00", fare="FrecciaYOUNG"), o(39, "09:00", n="9619", fare="FrecciaYOUNG"), o(29.9, "08:00", n="9611")], NOW)
        store.record(db, ui, [o(19.9, "06:15", ITALO, "9967", "Italo Giovani 14-29 anni")], NOW)
        vs, lowest, legs, total = check.views(w, db)
        self.assertEqual((lowest, len(vs[0]["ties"]), legs, total), (39, 2, None, None))
        self.assertTrue(check.live_ok(w, {ut: "OK", ui: "EMPTY"}))
        self.assertFalse(check.live_ok(w, {ut: "OK", ui: "BLOCKED"}))

    def test_fare_watch_with_nothing_on_sale_shows_cheapest_other_fare(self):
        db, w = store.connect(":memory:"), watch(fares=["T:FrecciaYOUNG"], ops=["T"])
        store.record(db, check.fetch_units(w)[0], [o(29.9, "08:00", n="9611")], NOW)
        vs, lowest, _, _ = check.views(w, db)
        self.assertEqual((lowest, vs[0]["other_fare"].price), (None, 29.9))

    def test_round_trip_total(self):
        db, w = store.connect(":memory:"), watch(pax="adult", ret={"d": ["2026-11-12"], "w": None}, ops=["T"])
        store.record(db, check.record_units(w, 0)[0], [o(44.9, "07:00")], NOW)
        store.record(db, check.record_units(w, 1)[0], [o(49.9, "18:00", day="2026-11-12")], NOW)
        _, lowest, legs, total = check.views(w, db)
        self.assertEqual((lowest, legs), (94.8, {"out": 44.9, "ret": 49.9}))


if __name__ == "__main__":
    unittest.main()
