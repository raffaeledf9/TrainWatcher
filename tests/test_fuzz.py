"""Random watches and offers through matching, ranking, round-trip pairing, alerts and rendering (fixed seed).
Properties: junk payloads are refused cleanly, round-trip pairs are travellable, messages fit Telegram's
4096-character limit, button data fits 64 bytes, operator text is escaped."""
import random
import unittest
from datetime import date, datetime, timedelta

from trainwatcher import alerts, model, prices, render, stations
from trainwatcher.offers import ITALO, TRENITALIA, Offer

TODAY = date(2026, 10, 7)
FARES = ["Base", "Super Economy", "FrecciaYOUNG", "Economy", "Low Cost", "Italo Giovani 14-29 anni", "A/R IN GIORNATA", "Andata e Ritorno", "Odd <b>promo"]


class Fuzz(unittest.TestCase):
    def setUp(self):
        self.r = random.Random(7)
        self.keys = {e["k"] for e in stations.entries()}

    def payload(self):
        r = self.r
        a, b = r.sample(["milano-tutte", "milano-centrale", "roma-termini", "roma-tutte", "napoli-centrale"], 2)
        d = TODAY + timedelta(days=r.randint(0, 120))
        days = r.choice([[str(d)], [str(d), str(d + timedelta(days=r.randint(1, 20)))], [str(d)[:7]]])
        p = {"v": 1, "from": a, "to": b, "out": {"d": days, "w": r.choice([None, ["06:00", "12:00"]])},
             "pax": r.choice(["adult", "young", "senior"]), "ops": r.choice([["T", "I"], ["T"], ["I"]]), "max": r.choice([None, 25]),
             "fares": r.choice([None, None, ["T:FrecciaYOUNG"], ["I:Economy", "T:Base"], ["TI:A/R same day"]])}
        if r.random() < .5:
            p["ret"] = {"d": r.choice([days, [str(d + timedelta(days=r.randint(0, 5)))]]), "w": None}
        if r.random() < .2:  # what a bad client could send
            p[r.choice(list(p))] = r.choice([None, "", [], {}, -1, "2026-13-40", ["x"], 10 ** 9])
        return p

    def offers(self, leg):
        r, out = self.r, []
        for _ in range(r.randint(0, 60)):
            d, h = r.choice(leg.dates()), r.randint(5, 20)
            op, fare = r.choice([TRENITALIA, ITALO]), r.choice(FARES)
            out.append(Offer(op, str(r.randint(9500, 9999)), "IT" if op == ITALO else "FR", f"{d}T{h:02d}:00", f"{d}T{h + 3:02d}:00",
                             r.choice(["MC_", "RG_", "Milano Centrale", "Roma Termini"]), "RMT", r.choice(["Standard", "Smart", "2ª Classe"]),
                             fare, r.choice([19.9, 29.9, round(r.uniform(5, 150), 2)]), r.choice([None, 3]), fare in ("A/R IN GIORNATA", "Andata e Ritorno")))
        return out

    def test_properties(self):
        valid = 0
        for _ in range(400):
            try:
                w = model.from_payload(self.payload(), 1, self.keys, TODAY)
            except model.Invalid:
                continue
            valid += 1
            views = []
            for leg in w.legs:
                matching = [o for o in self.offers(leg) if prices.matches(o, w, leg)]
                low, ties, others = prices.ranking(matching)
                views.append({"low": low, "ties": ties, "others": others, "matching": matching})
            total = legs = None
            if w.round_trip:
                total, a, b = prices.round_trip_total(views[0]["matching"], views[1]["matching"])
                if a:
                    self.assertGreater(b.dep, a.arr)
                    self.assertEqual(a.same_day_ar, b.same_day_ar)
                    self.assertTrue(not a.same_day_ar or a.operator == b.operator)
                views[0]["pair"], views[1]["pair"] = a, b
                legs = {"out": a and a.price, "ret": b and b.price}
            state, low = {}, total if w.round_trip else views[0]["low"]
            for step in range(3):
                today = TODAY + timedelta(days=step * 20)
                lw = self.r.choice([low, None, (low or 50) + 5])
                events, state = alerts.evaluate(w, state, lw, True, today, legs=legs)
                for lang in ("en", "it"):
                    msgs = [render.status(w, views, datetime(2026, 10, 7, 9), lang, today, total=total)]
                    rest = [e for e in events if e["kind"] != "baseline"]
                    if rest:
                        msgs.append(render.alert(w, rest, views, datetime(2026, 10, 7, 9), lang, today))
                    for text, kb in msgs:
                        self.assertLess(len(text), 4096)
                        self.assertNotIn("<b>promo", text)
                        self.assertTrue(all(len(b.get("callback_data", "").encode()) <= 64 for row in kb for b in row))
        self.assertGreater(valid, 50)


if __name__ == "__main__":
    unittest.main()
