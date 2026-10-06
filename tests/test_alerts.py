"""R5 alert rules as scenario tables: a price sequence in, the alert kinds out."""
import unittest
from datetime import date, datetime, timedelta

from trainwatcher import alerts, model

TODAY = date(2026, 10, 6)
KEYS = {"milano-tutte", "roma-termini"}


def watch(**kw):
    p = {"v": 1, "from": "milano-tutte", "to": "roma-termini", "out": {"d": ["2026-11-10"], "w": None}, "pax": "adult", "ops": ["T", "I"]}
    p.update(kw)
    return model.from_payload(p, 1, KEYS, TODAY)


def run(w, seq, today=TODAY, live=True):
    """seq: lowest prices (None = no matching offer). Returns the alert kinds per Check."""
    state, out = {}, []
    for low in seq:
        evs, state = alerts.evaluate(w, state, low, live, today)
        out.append([e["kind"] for e in evs])
    return out


class CheapestWatch(unittest.TestCase):
    def test_drops_only_below_lowest_already_alerted(self):
        self.assertEqual(run(watch(), [40, 39, 41, 39, 38, 38]),
                         [["baseline"], ["drop"], [], [], ["drop"], []])

    def test_max_gates_drops_and_under_max_rearms(self):
        w = watch(max=30)
        self.assertEqual(run(w, [40, 35, 29, 31, 29.5, 28]),
                         [["baseline"], [], ["drop"], [], ["under_max"], ["drop"]])

    def test_rises_switch_alerts_every_change(self):
        self.assertEqual(run(watch(rises=True), [40, 45, 40, 40]), [["baseline"], ["rise"], ["drop"], []])

    def test_sold_out_needs_a_live_check(self):
        w = watch()
        state = alerts.evaluate(w, {}, 40, True, TODAY)[1]
        evs, state2 = alerts.evaluate(w, state, None, False, TODAY)     # failed Check: proves nothing
        self.assertEqual((evs, state2["low"]), ([], 40))
        evs, state3 = alerts.evaluate(w, state2, None, True, TODAY)     # confirmed by a live Check
        self.assertEqual([e["kind"] for e in evs], ["gone"])
        self.assertEqual([e["kind"] for e in alerts.evaluate(w, state3, 44, True, TODAY)[0]], ["back"])


class FareWatch(unittest.TestCase):
    def setUp(self):
        self.w = watch(pax="young", fares=["T:FrecciaYOUNG"], max=30)

    def test_every_rise_and_drop_marked_against_max(self):
        self.assertEqual(run(self.w, [29, 39, 29, 29]), [["baseline"], ["rise"], ["drop"], []])
        evs, _ = alerts.evaluate(self.w, {"low": 29}, 39, True, TODAY)
        self.assertEqual((evs[0]["kind"], evs[0]["under_max"]), ("rise", False))

    def test_window_closed_vs_sold_out(self):
        far = alerts.evaluate(self.w, {"low": 29}, None, True, TODAY)[0][0]
        self.assertEqual(far["reason"], "sold_out")
        near_day = self.w.first_day - timedelta(days=5)                   # past the 11-day window
        self.assertEqual(alerts.evaluate(self.w, {"low": 29}, None, True, near_day)[0][0]["reason"], "window")

    def test_still_on_sale_past_the_window_only_once(self):
        d = self.w.first_day - timedelta(days=8)
        self.assertEqual(run(self.w, [29, 29], today=d), [["baseline", "still_on_sale"], []])

    def test_last_day_reminder(self):
        d = self.w.first_day - timedelta(days=11)
        self.assertEqual(run(self.w, [29, 29], today=d), [["baseline", "last_day"], []])


class Misc(unittest.TestCase):
    def test_night_is_silent(self):
        self.assertTrue(alerts.silent(datetime(2026, 11, 1, 23, 30)))
        self.assertTrue(alerts.silent(datetime(2026, 11, 1, 6, 59)))
        self.assertFalse(alerts.silent(datetime(2026, 11, 1, 7, 0)))

    def test_round_trip_total_and_legs_travel_with_the_event(self):
        w = watch(ret={"d": ["2026-11-12"], "w": None})
        _, s = alerts.evaluate(w, {}, 64.8, True, TODAY, legs={"out": 34.9, "ret": 29.9})
        evs, _ = alerts.evaluate(w, s, 59.8, True, TODAY, legs={"out": 29.9, "ret": 29.9})
        self.assertEqual((evs[0]["kind"], evs[0]["prev_legs"]["out"], evs[0]["legs"]["out"]), ("drop", 34.9, 29.9))


if __name__ == "__main__":
    unittest.main()
