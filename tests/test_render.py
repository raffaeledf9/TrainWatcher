"""R6 messages: the approved layouts, in both languages."""
import dataclasses
import importlib.util
import unittest
from datetime import date, datetime

from trainwatcher import alerts, model, prices, render
from trainwatcher.offers import ITALO, TRENITALIA, Offer

TODAY = date(2026, 10, 6)
AT = datetime(2026, 10, 6, 14, 5)
KEYS = {"milano-tutte", "roma-termini", "napoli-centrale"}


def watch(**kw):
    p = {"v": 1, "from": "milano-tutte", "to": "roma-termini", "out": {"d": ["2026-11-10"], "w": None}, "pax": "adult", "ops": ["T", "I"]}
    p.update(kw)
    w = model.from_payload(p, 1, KEYS, TODAY)
    w.id = 7
    return w


def o(p, dep, op=ITALO, n="9967", fare="eXtra Magic", cls="Smart", seats=None, cat=None):
    origin, dest = ("MC_", "RMT") if op == ITALO else ("Milano Centrale", "Roma Termini")  # as the operators return them
    return Offer(op, n, cat or ("IT" if op == ITALO else "FR"), "2026-11-10T" + dep, "2026-11-10T" + dep[:2] + ":59", origin, dest, cls, fare, p, seats)


OFFERS = [o(29.9, "06:15", seats=8), o(29.9, "10:40", n="9971"), o(37.9, "07:00", TRENITALIA, "9607", "FrecciaDAYS", "Standard"),
          o(44.9, "08:00", TRENITALIA, "9611", "Super Economy", "Standard"), o(47.9, "05:40", n="9907", fare="Low Cost")]


def view(offers):
    low, ties, others = prices.ranking(offers)
    return {"low": low, "ties": ties, "others": others}


class StatusView(unittest.TestCase):
    def test_style_a_english(self):
        text, kb = render.status(watch(max=25), [view(OFFERS)], AT, "en", TODAY)
        for part in ("🚄 <b>Milano (all) → Roma Termini</b>", "📅 Tue 10 Nov · 👤 Adult · 💶 Cheapest fare", "<b>€29.90</b> lowest · 2 trains",
                     "• 06:15 Centrale → 06:59 <b>Italo 9967</b> · Smart · eXtra Magic · 8 left", "<i>Next cheapest</i>",
                     "• €37.90 07:00 Centrale FR 9607 · FrecciaDAYS",
                     "⚠️ Lowest is above your max <b>€25.00</b>", "🔄 last check 14:05"):
            self.assertIn(part, text)
        self.assertEqual([b["text"] for b in kb[0]], ["🎫 Book on Italo", "🎫 Book on Trenitalia"])
        self.assertEqual([b["callback_data"] for b in kb[1]], ["hi:7", "nw:7", "de:7"])
        self.assertIn("osc=MI0&dsc=RMT", kb[0][0]["url"])

    def test_italian_formatting(self):
        text, kb = render.status(watch(), [view(OFFERS)], AT, "it", TODAY)
        for part in ("Milano (tutte) → Roma Termini", "mar 10 nov · 👤 Adulto · 💶 Più economica", "<b>29,90 €</b> il più basso · 2 treni",
                     "🔄 ultimo controllo 14:05"):
            self.assertIn(part, text)
        self.assertEqual(kb[1][0]["text"], "📈 Storico")

    def test_search_result(self):
        text, kb = render.status(watch(), [view(OFFERS)], AT, "en", TODAY, search=True, search_id=3)
        self.assertIn("🔎 <b>Search</b> · MIL → ROM · Tue 10 Nov", text)
        self.assertIn("🔄 live search, not saved", text)
        self.assertEqual(kb[-1], [{"text": "➕ Watch this", "callback_data": "wt:3"}])


class Alerts(unittest.TestCase):
    def test_fare_watch_rise_strikes_old_price_and_warns(self):
        w = watch(pax="young", fares=["T:FrecciaYOUNG"], max=30)
        fy = [o(39, "07:00", TRENITALIA, "9607", "FrecciaYOUNG", "Standard"), o(39, "09:00", TRENITALIA, "9619", "FrecciaYOUNG", "Standard")]
        evs, _ = alerts.evaluate(w, {"low": 29}, 39, True, TODAY)
        text, kb = render.alert(w, evs, [view(fy)], AT, "en", TODAY)
        for part in ("📈 <b>Price up</b> · FrecciaYOUNG", "<b>€39.00</b> <s>€29.00</s> — Frecciarossa 9607 07:00", "⚠️ above your max €30.00 — buy soon",
                     "🛒 Purchase window closes Fri 30 Oct"):
            self.assertIn(part, text)
        self.assertEqual([b["text"] for b in kb[0]], ["🎫 07:00 FR 9607", "🎫 09:00 FR 9619"])

    def test_gone_and_still_on_sale_italian(self):
        w = watch(pax="young", fares=["T:FrecciaYOUNG"])
        text, _ = render.alert(w, [{"kind": "gone", "reason": "window", "low": None, "prev": 29}], [view([])], AT, "it", TODAY)
        self.assertIn("🚫 <b>FrecciaYOUNG non più disponibile</b>", text)
        self.assertIn("vendita chiusa (11 giorni prima della partenza)", text)
        text, _ = render.alert(w, [{"kind": "still_on_sale", "low": 29, "prev": 29}], [view([o(29, "07:00", TRENITALIA, "9607", "FrecciaYOUNG", "Standard")])], AT, "it", TODAY)
        self.assertIn("La vendita dovrebbe essere terminata, ma da un controllo online risulta ancora disponibile", text)

    def test_round_trip_alert(self):
        w = watch(ret={"d": ["2026-11-12"], "w": None})
        out, ret = [o(29.9, "06:15")], [o(29.9, "18:40", n="9998")]
        e = {"kind": "drop", "low": 59.8, "prev": 64.8, "legs": {"out": 29.9, "ret": 29.9}, "prev_legs": {"out": 34.9, "ret": 29.9}}
        text, _ = render.alert(w, [e], [view(out), view(ret)], AT, "en", TODAY)
        for part in ("📉 <b>Round trip cheaper</b>", "Total <b>€59.80</b> <s>€64.80</s>", "→ Outbound changed: <b>€29.90</b> <s>€34.90</s>",
                     "← Return: <b>€29.90</b>", "(unchanged)"):
            self.assertIn(part, text)


class ListAndLabels(unittest.TestCase):
    def test_list_buttons_carry_the_info(self):
        text, kb = render.watch_list([(watch(), OFFERS[0], 29.9, AT)], "en", TODAY)
        self.assertIn("1️⃣ MIL → ROM · Tue 10 Nov · 🔄 last check 14:05", text)
        self.assertEqual(kb[0][0]["text"], "1️⃣ Milano (all) → Roma Termini · Tue 10 Nov · eXtra Magic €29.90")
        self.assertEqual(kb[0][0]["callback_data"], "st:7")

    def test_day_labels(self):
        mk = lambda d: model.Leg("a", "b", d)
        self.assertEqual(render.days_label(mk(["2026-11-12", "2026-11-15"]), "en", TODAY), "12–15 Nov")
        self.assertEqual(render.days_label(mk(["2026-11-28", "2026-12-03"]), "en", TODAY), "28 Nov – 3 Dec")
        self.assertEqual(render.days_label(mk(["2026-11"]), "it", TODAY), "nov 2026")
        self.assertEqual(render.days_label(mk(["2027-01-10"]), "en", TODAY), "Sun 10 Jan '27")


class CityGroups(unittest.TestCase):
    def test_station_shown_only_at_group_ends(self):
        rg = dataclasses.replace(o(29.9, "06:25"), origin="RG_")
        leg = watch().legs[0]
        self.assertEqual(render.times(rg, leg), "06:25 Rogoredo → 06:59")
        self.assertEqual(render.departs(rg, leg), "06:25 Rogoredo")
        single = watch(**{"from": "napoli-centrale"}).legs[0]
        self.assertEqual(render.times(o(29.9, "06:15"), single), "06:15→06:59")
        back = watch(ret={"d": ["2026-11-12"], "w": None}).legs[1]  # Roma Termini → Milano (all)
        self.assertEqual(render.times(dataclasses.replace(o(29.9, "18:00"), origin="RMT", destination="RG_"), back), "18:00 → 18:59 Rogoredo")

    def test_trenitalia_names_lose_the_city(self):
        self.assertEqual(render.stop("Milano Porta Garibaldi", "milano-tutte"), "Porta Garibaldi")
        self.assertEqual(render.stop("Roma Termini", "roma-termini"), None)


class ChartLines(unittest.TestCase):
    def test_overlap(self):
        from trainwatcher.charts import _overlap
        a = [("2026-10-01T10:00", 34.9), ("2026-10-02T10:00", 29.9)]
        self.assertTrue(_overlap(a, [("2026-10-01T10:00", 39.9), ("2026-10-02T12:00", 29.9)]))
        self.assertFalse(_overlap(a, [("2026-10-01T10:00", 39.9), ("2026-10-02T12:00", 31.9)]))


@unittest.skipUnless(importlib.util.find_spec("matplotlib"), "matplotlib not installed")
class Chart(unittest.TestCase):
    def test_png(self):
        from trainwatcher.charts import chart_png
        png = chart_png([("IT 9967  06:15 → 09:24", [("2026-10-01T10:00", 34.9), ("2026-10-03T10:00", 29.9), ("2026-10-04T10:00", None)])],
                        "MIL → ROM · Tue 10 Nov", "it", max_price=25)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")


if __name__ == "__main__":
    unittest.main()
