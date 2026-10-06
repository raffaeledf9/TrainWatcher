"""R4 core: watch validation, price logic, scheduling, storage."""
import unittest
from datetime import date, datetime, timedelta

from trainwatcher import model, prices, schedule, store
from trainwatcher.offers import ITALO, TRENITALIA, Offer

TODAY = date(2026, 10, 6)
KEYS = {"milano-tutte", "roma-termini", "napoli-centrale"}


def payload(**kw):
    p = {"v": 1, "lang": "en", "from": "milano-tutte", "to": "roma-termini", "out": {"d": ["2026-11-10"], "w": None},
         "ret": None, "pax": "adult", "fares": None, "cls": None, "ops": ["T", "I"], "max": None, "rises": False}
    p.update(kw)
    return p


def offer(price, dep="2026-11-10T07:00", op=TRENITALIA, train="9607", fare="Super Economy", cls="Standard", ar=False, seats=None):
    return Offer(op, train, "FR" if op == TRENITALIA else "IT", dep, dep[:11] + "10:10", "Milano Centrale", "Roma Termini", cls, fare, price, seats, ar)


class WatchValidation(unittest.TestCase):
    def w(self, **kw):
        return model.from_payload(payload(**kw), 1, KEYS, TODAY)

    def test_valid_one_way(self):
        w = self.w()
        self.assertEqual((w.kind, w.round_trip, w.first_day), ("cheapest", False, date(2026, 11, 10)))

    def test_rejections(self):
        for bad in (dict(to="milano-tutte"), dict(to="nowhere"), dict(out={"d": ["2026-09-01"]}), dict(pax="child"),
                    dict(fares=["T:Nope"]), dict(fares=["T:FrecciaYOUNG"]), dict(max=-3), dict(ops=[]), dict(v=2),
                    dict(out={"d": ["2026-11-10"], "w": ["12:00", "06:00"]}),
                    dict(ret={"d": ["2026-11-01"], "w": None}, out={"d": ["2026-11-10"], "w": None})):
            with self.subTest(bad=bad), self.assertRaises(model.Invalid):
                self.w(**bad)

    def test_young_fare_ok_for_young(self):
        self.assertEqual(self.w(pax="young", fares=["T:FrecciaYOUNG"]).kind, "fare")

    def test_days_kinds(self):
        self.assertEqual(len(model.Leg("a", "b", ["2026-11"]).dates()), 30)
        self.assertEqual(len(model.Leg("a", "b", ["2026-11-10", "2026-11-16"]).dates()), 7)
        lg = model.Leg("a", "b", ["2026-11-10"], ["06:00", "12:00"])
        self.assertTrue(lg.accepts("2026-11-10T06:00") and lg.accepts("2026-11-10T12:00"))
        self.assertFalse(lg.accepts("2026-11-10T12:01") or lg.accepts("2026-11-11T07:00"))

    def test_rises_only_for_cheapest(self):
        self.assertFalse(self.w(rises=True, fares=["T:Base"]).rises)
        self.assertTrue(self.w(rises=True).rises)


class PriceLogic(unittest.TestCase):
    def setUp(self):
        self.cheap = model.from_payload(payload(), 1, KEYS, TODAY)

    def test_young_fares_hidden_from_adults_in_cheapest_watch(self):
        leg = self.cheap.legs[0]
        self.assertFalse(prices.matches(offer(29, fare="FrecciaYOUNG"), self.cheap, leg))
        self.assertTrue(prices.matches(offer(44.9), self.cheap, leg))

    def test_fare_watch_has_no_fallback(self):
        w = model.from_payload(payload(pax="young", fares=["T:FrecciaYOUNG"]), 1, KEYS, TODAY)
        self.assertTrue(prices.matches(offer(39, fare="FrecciaYOUNG"), w, w.legs[0]))
        self.assertFalse(prices.matches(offer(19.9, fare="Super Economy"), w, w.legs[0]))

    def test_italo_fare_names_and_operator_filter(self):
        w = model.from_payload(payload(fares=["I:Low Cost"], ops=["I"]), 1, KEYS, TODAY)
        self.assertTrue(prices.matches(offer(43.9, op=ITALO, fare="Low Cost", cls="Smart"), w, w.legs[0]))
        self.assertFalse(prices.matches(offer(43.9, fare="Economy"), w, w.legs[0]))

    def test_ar_fares_only_for_same_day_round_trips(self):
        one = self.cheap
        self.assertFalse(prices.matches(offer(66.5, fare="A/R IN GIORNATA", ar=True), one, one.legs[0]))
        rt = model.from_payload(payload(ret={"d": ["2026-11-10"], "w": None}), 1, KEYS, TODAY)
        self.assertTrue(rt.same_day)
        self.assertTrue(prices.matches(offer(66.5, fare="A/R IN GIORNATA", ar=True), rt, rt.legs[0]))

    def test_ranking_keeps_all_ties(self):
        offers = [offer(29.9, "2026-11-10T06:15", ITALO, "9967"), offer(29.9, "2026-11-10T10:40", ITALO, "9971"),
                  offer(37.9, "2026-11-10T07:00"), offer(44.9, "2026-11-10T08:00", train="9611"), offer(47.9, "2026-11-10T05:40", ITALO, "9907"),
                  offer(49.9, "2026-11-10T09:00", train="9619"), offer(35.0, "2026-11-10T06:15", ITALO, "9967")]
        low, ties, others = prices.ranking(offers, top=5)
        self.assertEqual((low, len(ties), [o.price for o in others]), (29.9, 2, [37.9, 44.9, 47.9]))
        many = [offer(19.9, f"2026-11-10T0{h}:00", train=str(h)) for h in range(7)]
        self.assertEqual(len(prices.ranking(many, top=5)[1]), 7)  # ties are never cut

    def test_round_trip_total_prefers_cheaper_family(self):
        out = [offer(44.9), offer(66.5, fare="A/R IN GIORNATA", ar=True)]
        ret = [offer(49.9, "2026-11-10T18:00"), offer(66.5, "2026-11-10T18:00", fare="A/R IN GIORNATA", ar=True)]
        self.assertEqual(prices.round_trip_total(out, ret)[0], 94.8)
        out[0] = offer(80.0)
        self.assertEqual(prices.round_trip_total(out, ret)[0], 129.9)
        self.assertEqual(prices.round_trip_total(out[1:], ret[:1])[0], None)  # A/R needs both legs


class Scheduling(unittest.TestCase):
    def test_cadence(self):
        self.assertEqual([schedule.cadence_minutes(TODAY + timedelta(days=d), TODAY) for d in (1, 3, 4, 14, 15, 45, 46)],
                         [15, 15, 30, 30, 120, 120, 360])

    def test_units_and_shared_fetches(self):
        a = model.from_payload(payload(out={"d": ["2026-11-10", "2026-11-12"], "w": None}), 1, KEYS, TODAY)
        b = model.from_payload(payload(), 1, KEYS, TODAY)
        self.assertEqual(len(schedule.units(a)), 6)      # 3 days x 2 operators
        take, fetch, deferred = schedule.plan([a, b], datetime(2026, 10, 6, 12, 0))
        self.assertEqual((len(take), len(fetch), deferred), (2, 6, []))  # b's units are already in a's

    def test_same_day_round_trip_is_one_unit_per_operator(self):
        rt = model.from_payload(payload(ret={"d": ["2026-11-10"], "w": None}), 1, KEYS, TODAY)
        self.assertEqual(len(schedule.units(rt)), 2)

    def test_budget_defers_and_skipped_go_first(self):
        month = [model.from_payload(payload(out={"d": ["2026-11"], "w": None}, to=t), 1, KEYS, TODAY) for t in ("roma-termini", "napoli-centrale")]
        take, _, deferred = schedule.plan(month, datetime(2026, 10, 6), budget_s=40)
        self.assertEqual((len(take), len(deferred)), (1, 1))
        deferred[0].skipped = True
        take2, _, _ = schedule.plan(month, datetime(2026, 10, 6), budget_s=40)
        self.assertIs(take2[0], deferred[0])

    def test_load_estimate(self):
        w = model.from_payload(payload(out={"d": ["2026-11"], "w": None}), 1, KEYS, TODAY)
        self.assertLess(schedule.load([w], TODAY), schedule.WARN_LOAD)
        self.assertGreater(schedule.load([w] * 80, TODAY), schedule.WARN_LOAD)  # ~67 month watches = 70 %


class Storage(unittest.TestCase):
    def setUp(self):
        self.db = store.connect(":memory:")
        self.now = datetime(2026, 10, 6, 12, 0)
        self.unit = ("T", "milano-tutte", "roma-termini", date(2026, 11, 10), "adult", None)

    def test_watch_roundtrip_and_due(self):
        w = model.from_payload(payload(), 1, KEYS, TODAY)
        wid = store.add_watch(self.db, w, self.now)
        got = store.get_watch(self.db, wid)
        self.assertEqual((got.legs[0].days, got.kind), (["2026-11-10"], "cheapest"))
        self.assertEqual(len(store.due_watches(self.db, self.now)), 1)
        store.mark_checked(self.db, got, self.now, self.now + timedelta(minutes=120), {"low": 29.9})
        self.assertEqual(store.due_watches(self.db, self.now), [])
        self.assertEqual(store.get_watch(self.db, wid).alert_state, {"low": 29.9})

    def test_change_only_history_and_vanishing(self):
        a, b = offer(44.9), offer(37.9, "2026-11-10T07:30", train="9609")
        self.assertEqual(len(store.record(self.db, self.unit, [a, b], self.now)[0]), 2)
        changed, vanished = store.record(self.db, self.unit, [a, b], self.now + timedelta(minutes=30))
        self.assertEqual((changed, vanished), ([], []))                       # nothing changed, nothing stored
        changed, vanished = store.record(self.db, self.unit, [offer(39.9)], self.now + timedelta(minutes=60))
        self.assertEqual((len(changed), len(vanished)), (1, 1))               # a rose, b sold out
        key = store.offer_key(self.unit, a)
        self.assertEqual([p for _, p in store.history(self.db, [key])[key]], [44.9, 39.9])
        self.assertEqual(len(store.current_offers(self.db, self.unit)), 1)


if __name__ == "__main__":
    unittest.main()
