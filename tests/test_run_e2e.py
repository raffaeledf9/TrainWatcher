"""The whole job (run.main) over several days, with fake operators, a fake Worker and a fake Telegram:
forms, searches, callbacks, price moves, the travel day and the day after. Catches what unit tests can't:
the pieces not fitting together."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

from trainwatcher import check, run
from trainwatcher.offers import EMPTY, ITALO, OK, TRENITALIA, Offer, SearchResult

UTC = timezone.utc
T_NAMES = {830001650: ["Milano Centrale", "Milano Rogoredo"], 830001700: ["Milano Centrale"], 830008409: ["Roma Termini"]}
I_CODES = {"MI0": ["MC_", "RG_"], "MC_": ["MC_"], "RMT": ["RMT"]}


class World:
    """Operators, Worker and Telegram as the job sees them."""

    def __init__(self):
        self.now = None                 # aware UTC
        self.price = 30.0               # base price; tests move it
        self.queue, self.next_id, self.sent, self.snaps = [], 1, [], {}

    def local(self):
        return self.now.astimezone(run.ROME)

    def trains(self, origins, day, first_dep, op):
        """Two trains a day, each boarding at every station of the origin; on the travel day departed ones are gone."""
        out = []
        for n, (h, step) in enumerate(((7, 10), (18, 10))):
            for k, st in enumerate(origins):
                dep = f"{day}T{h:02d}:{k * step:02d}"
                if day == str(self.local().date()) and dep[11:] <= f"{self.local():%H:%M}":
                    continue
                out.append((f"{first_dep + n}", dep, f"{day}T{h + 3:02d}:00", st))
        return out

    def trenitalia(self, a, b, day, window=None, ar_return=None):
        offers = []
        for train, dep, arr, st in self.trains(T_NAMES[a], str(day), 9600, TRENITALIA):
            for fare, p in (("Base", 90.0), ("Super Economy", self.price), ("FrecciaYOUNG", self.price - 5)):
                offers.append(Offer(TRENITALIA, train, "FR", dep, arr, st, T_NAMES[b][0], "Standard", fare, p, 50))
            if ar_return:
                offers.append(Offer(TRENITALIA, train, "FR", dep, arr, st, T_NAMES[b][0], "Standard", "A/R IN GIORNATA", 63.0, None, True))
        return SearchResult(OK if offers else EMPTY, offers)

    def trenitalia_rt(self, a, b, day, out_window=None, ret_window=None):
        return self.trenitalia(a, b, day, ar_return="23:59"), self.trenitalia(b, a, day, ar_return="23:59")

    def italo_offers(self, a, b, day, pax, ar=False):
        offers = []
        for train, dep, arr, st in self.trains(I_CODES[a], str(day), 9900, ITALO):
            fares = [("Economy", self.price + 2)] + ([("Italo Giovani 14-29 anni", self.price - 8)] if pax == "young" else [])
            fares += [("Andata e Ritorno", 25.0)] if ar else []
            offers += [Offer(ITALO, train, "IT", dep, arr, st, I_CODES[b][0], "Smart", f, p, 9, f == "Andata e Ritorno") for f, p in fares]
        return SearchResult(OK if offers else EMPTY, offers)

    def worker(self, path, body=None):
        if path == "/job/take":
            items = [q for q in self.queue if not q.get("taken")]
            for q in items:
                q["taken"] = True
            return [{k: q[k] for k in ("id", "user_id", "kind", "payload")} for q in items]
        if path == "/job/ack":
            self.queue = [q for q in self.queue if q["id"] not in body["ids"]]
        if path == "/job/snapshot":
            self.snaps.update({r["key"]: json.loads(r["body"]) for r in body["rows"]})
        return {"ok": True}

    def telegram(self, method, **p):
        self.sent.append(p)
        return {"ok": True}

    def enqueue(self, uid, kind, payload):
        self.queue.append({"id": self.next_id, "user_id": uid, "kind": kind, "payload": json.dumps(payload)})
        self.next_id += 1


class FakeSession:
    def __init__(self, world):
        self.w = world

    def search(self, origin, dest, day, window=None, passenger="adult"):
        return self.w.italo_offers(origin, dest, day, passenger)

    def round_trip(self, origin, dest, day, return_day, passenger="adult"):
        return self.w.italo_offers(origin, dest, day, passenger, ar=True), self.w.italo_offers(dest, origin, return_day, passenger, ar=True)


def form(mode, **data):
    d = {"v": 1, "from": "milano-tutte", "to": "roma-termini", "out": {"d": ["2026-11-10"], "w": None}, "ops": ["T", "I"]}
    d.update(data)
    return {"mode": mode, "data": d}


class FullRuns(unittest.TestCase):
    def setUp(self):
        self.w = w = World()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        clock = type("Clock", (datetime,), {"now": classmethod(lambda cls, tz=None: w.now.astimezone(tz) if tz else w.now.replace(tzinfo=None))})
        for p in (mock.patch.dict(os.environ, {"OWNER_CHAT_ID": "1", "STATE_DB": os.path.join(tmp.name, "s.db"), "GITHUB_RUN_NUMBER": "0"}),
                  mock.patch.object(run, "datetime", clock), mock.patch.object(run, "worker", w.worker),
                  mock.patch.object(run, "telegram", w.telegram), mock.patch.object(check.trenitalia, "search", w.trenitalia),
                  mock.patch.object(check.trenitalia, "same_day_round_trip", w.trenitalia_rt),
                  mock.patch.object(check.italo, "Session", lambda workers=3: FakeSession(w)),
                  mock.patch("builtins.print")):
            p.start()
            self.addCleanup(p.stop)

    def run_at(self, utc):
        self.w.now, self.w.sent = utc.replace(tzinfo=UTC), []
        self.assertEqual(run.main(), 0)
        return [m["text"] for m in self.w.sent]

    def test_days_of_use(self):
        w = self.w
        # Oct 7: language, a FrecciaYOUNG fare watch, a same-day round trip, a month search, a broken form
        w.enqueue(1, "lang", {"lang": "it"})
        w.enqueue(1, "form", form("watch", pax="young", fares=["T:FrecciaYOUNG"], max=30))
        w.enqueue(1, "form", form("watch", out={"d": ["2026-11-12"], "w": None}, ret={"d": ["2026-11-12"], "w": None}, max=100))
        w.enqueue(1, "form", form("search", out={"d": ["2026-11"], "w": None}))
        w.enqueue(1, "form", form("watch", to="milano-tutte"))
        texts = self.run_at(datetime(2026, 10, 7, 8, 0))
        self.assertEqual(len(texts), 4, texts)                       # 2 statuses, 1 search, 1 refusal
        fare_status = next(t for t in texts if "FrecciaYOUNG" in t and "🚄" in t)
        self.assertIn("€25.00", fare_status.replace("25,00 €", "€25.00"))
        self.assertIn("Rogoredo", fare_status)                      # Milano (tutte): the boarding station is named
        rt_status = next(t for t in texts if "⇄" in t and "🚄" in t)
        self.assertIn("50,00 €", rt_status)                          # Italo A/R 25 + 25 beats one-way 30 + 30
        search = next(m for m in w.sent if "🔎" in m["text"])
        sid = next(b["callback_data"] for row in search["reply_markup"]["inline_keyboard"] for b in row if b.get("callback_data", "").startswith("wt:"))
        self.assertTrue(any("partenza e arrivo coincidono" in t for t in texts))  # the reason in Italian too
        buttons = next(m for m in w.sent if "FrecciaYOUNG" in m["text"])["reply_markup"]["inline_keyboard"][0]
        self.assertEqual([b["text"] for b in buttons], ["🎫 Prenota su Trenitalia"])  # a Trenitalia fare: no Italo button
        self.assertTrue(all(len(m["text"]) < 4096 for m in w.sent))  # 120 trains tie on a flat month: still one message
        self.assertIn("• lun 30 nov: 07:00 Centrale, 07:10 Rogoredo, 18:00 Centrale, 18:10 Rogoredo", search["text"])  # all 120, by day
        self.assertIn("1:list", w.snaps)
        self.assertIsNotNone(w.snaps["next_due"])

        # Oct 7, 2 h later: prices up 5 -> the fare watch alerts; "watch this" twice; a stranger deletes nothing
        w.price += 5
        w.enqueue(1, "callback", {"data": sid})
        w.enqueue(1, "callback", {"data": sid})
        w.enqueue(2, "callback", {"data": "dy:1"})
        texts = self.run_at(datetime(2026, 10, 7, 10, 30))
        self.assertTrue(any("Prezzo in aumento" in t and "FrecciaYOUNG" in t for t in texts), texts)
        self.assertEqual(sum("Monitoraggio creato" in t for t in texts), 1)
        self.assertEqual(sum(1 for k in w.snaps if k.startswith("1:status:")), 3)

        # Nov 10, travel day of watch 1, 15:00: the morning trains are gone; that is not a price change
        texts = self.run_at(datetime(2026, 11, 10, 14, 0))
        self.assertFalse(any("FrecciaYOUNG" in t and ("Prezzo" in t or "non più disponibile" in t) for t in texts), texts)
        status1 = "\n".join(p["text"] for p in w.snaps["1:status:1"]["parts"])
        self.assertIn("18:00", status1)                              # the status still lists the evening trains
        self.assertNotIn("07:00", status1)

        # Nov 11: watch 1 is over and shows in /past with its chart button
        self.run_at(datetime(2026, 11, 11, 8, 0))
        past = w.snaps["1:past"]
        self.assertIn("hi:1", json.dumps(past["kb"]))
        self.assertNotIn("st:1", json.dumps(w.snaps["1:list"]["kb"]))


class Friends(FullRuns):
    def test_days_of_use(self):
        pass

    def test_a_friend_from_first_watch_to_removal(self):
        w = self.w
        to = lambda chat: [m["text"] for m in w.sent if m["chat_id"] == chat]
        for day in range(10, 16):                                  # 6 watches: the 6th is over the friend's cap
            w.enqueue(42, "form", form("watch", out={"d": [f"2026-11-{day}"], "w": None}, pax="young", fares=["T:FrecciaYOUNG"]))
        w.enqueue(1, "form", form("watch"))
        w.enqueue(42, "revoke", {"user_id": 1})                  # a friend can't stop anyone's watches
        self.run_at(datetime(2026, 10, 7, 8, 0))
        self.assertEqual(len(to(42)), 5 + 1)                       # 5 statuses + the cap message
        self.assertTrue(any("at most 5" in t for t in to(42)))
        self.assertEqual(len(to(1)), 1)
        self.assertEqual(len(w.snaps["42:list"]["kb"]), 5)
        self.assertEqual(len(w.snaps["1:list"]["kb"]), 1)

        w.price += 5                                               # the friend's fare watches alert, in the friend's chat
        self.run_at(datetime(2026, 10, 7, 10, 30))
        self.assertEqual(sum("Price up" in t for t in to(42)), 5)
        self.assertFalse(any("FrecciaYOUNG" in t for t in to(1)))

        w.enqueue(1, "revoke", {"user_id": 42})                  # the Owner removes the friend
        w.price += 5
        self.run_at(datetime(2026, 10, 7, 13, 0))
        self.assertEqual(to(42), [])                               # nothing more for them
        self.assertEqual(w.snaps["42:list"]["kb"], [])
        self.assertEqual(len(w.snaps["1:list"]["kb"]), 1)          # the Owner's watch untouched

    def test_owner_hears_once_when_capacity_runs_short(self):
        notices = lambda: [m["text"] for m in self.w.sent if m["chat_id"] == 1 and "capacity" in m["text"]]
        with mock.patch.object(run.schedule, "load", return_value=0.8):
            self.run_at(datetime(2026, 10, 7, 8, 0))
            self.assertEqual(len(notices()), 1)
            self.run_at(datetime(2026, 10, 7, 9, 0))
            self.assertEqual(notices(), [])
        with mock.patch.object(run.schedule, "load", return_value=0.5):
            self.run_at(datetime(2026, 10, 7, 10, 0))
        with mock.patch.object(run.schedule, "load", return_value=0.8):
            self.run_at(datetime(2026, 10, 7, 11, 0))
            self.assertEqual(len(notices()), 1)                    # re-armed after dropping below 60 %


class CustomPeriod(FullRuns):
    def test_days_of_use(self):
        pass

    def test_three_months_three_messages(self):
        w = self.w
        w.enqueue(1, "form", form("watch", out={"d": ["2026-11-20", "2027-01-15"], "w": None}))
        self.run_at(datetime(2026, 10, 7, 8, 0))
        parts = [m for m in w.sent if "🚄" in m["text"]]
        self.assertEqual([("(1/3)" in p["text"], "(2/3)" in p["text"], "(3/3)" in p["text"]) for p in parts],
                         [(True, False, False), (False, True, False), (False, False, True)])
        self.assertIn("20 Nov", parts[0]["text"])                  # each part lists its own month only
        self.assertNotIn("Dec", parts[0]["text"].split("\n", 2)[2])
        self.assertIn("15 Jan", parts[2]["text"])
        kb = [[b["text"] for b in row] for row in parts[2]["reply_markup"]["inline_keyboard"]]
        self.assertIn("🔄 Check now", sum(kb, []))                  # the watch's buttons on the last part only
        self.assertNotIn("🔄 Check now", sum([[b["text"] for b in r] for r in parts[0].get("reply_markup", {}).get("inline_keyboard", [])], []))
        self.assertEqual(len(w.snaps["1:status:1"]["parts"]), 3)
        w.price -= 3
        self.run_at(datetime(2026, 10, 7, 11, 0))
        alerts = [m for m in w.sent if "New low" in m["text"]]
        self.assertEqual(len(alerts), 1)                           # one alert message, all 57 days summarised
        self.assertLess(len(alerts[0]["text"]), 4096)
        self.assertEqual([b["text"] for b in alerts[0]["reply_markup"]["inline_keyboard"][0]], ["🎫 Book on Italo", "🎫 Book on Trenitalia"])


class PoisonCommand(FullRuns):
    def test_days_of_use(self):
        pass

    def test_a_malformed_command_neither_stops_the_run_nor_comes_back(self):
        self.w.enqueue(1, "form", {"mode": "watch", "data": {"v": 1, "from": {}, "to": [], "out": {"d": [{}], "w": [1]}}})
        self.w.enqueue(1, "lang", {"lang": "xx"})
        self.w.enqueue(1, "callback", {"data": "nw:abc"})
        self.w.enqueue(1, "form", form("watch"))                     # a good one after the bad ones still works
        texts = self.run_at(datetime(2026, 10, 7, 8, 0))
        self.assertEqual(self.w.queue, [])                           # all acknowledged: nothing to retry forever
        self.assertTrue(any("bad form" in t for t in texts))
        self.assertTrue(any("🚄" in t for t in texts))


if __name__ == "__main__":
    unittest.main()
