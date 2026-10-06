"""Connector tests, offline: parsers on real captured responses (tests/fixtures, 2026-10-06, travel day 2026-11-10),
mocked HTTP for classification, paging and the Italo session flow."""
import copy
import gzip
import io
import json
import time
import unittest
import urllib.error
from http.cookiejar import Cookie
from pathlib import Path
from unittest import mock

from trainwatcher.offers import BLOCKED, BROKEN, EMPTY, OK, SearchResult
from trainwatcher.operators import http, italo, trenitalia

FIX = Path(__file__).parent / "fixtures"
DAY, MI, RM = "2026-11-10", 830001700, 830008409


def fixture(name):
    return json.loads(gzip.decompress((FIX / name).read_bytes()))


def trains(offers):
    return {(o.train, o.dep) for o in offers}


class HttpTest(unittest.TestCase):
    def test_classify(self):
        cases = [((403, "<html>"), BLOCKED), ((429, {}), BLOCKED), ((200, "<!DOCTYPE html><html>wait"), BLOCKED),
                 ((503, "<html>down</html>"), BROKEN), ((500, {}), BROKEN), ((None, "TimeoutError"), BROKEN),
                 ((400, {"message": "x"}), BROKEN), ((200, "plain text"), BROKEN), ((200, {"solutions": []}), OK)]
        for (status, body), want in cases:
            self.assertEqual(http.classify(status, body)[0], want, (status, body))

    def test_request_retries_network_error_once_and_gunzips(self):
        ok = io.BytesIO(gzip.compress(b'{"a": 1}'))
        ok.status, ok.headers = 200, {"Content-Encoding": "gzip"}
        opener = mock.Mock()
        opener.open.side_effect = [urllib.error.URLError("reset"), ok]
        with mock.patch("time.sleep"):
            self.assertEqual(http.request("GET", "https://x.test/", opener=opener), (200, {"a": 1}))
            opener.open.side_effect = urllib.error.URLError("down")
            self.assertEqual(http.request("GET", "https://x.test/", opener=opener), (None, "URLError"))

    def test_request_returns_http_errors_without_retry(self):
        opener = mock.Mock()
        opener.open.side_effect = urllib.error.HTTPError("https://x.test/", 503, "x", {}, io.BytesIO(b"oops"))
        self.assertEqual(http.request("GET", "https://x.test/", opener=opener), (503, "oops"))
        self.assertEqual(opener.open.call_count, 1)

    def test_run_parallel_keeps_order_and_halves_after_blocked(self):
        active, seen = 0, []

        def ok(i):
            def call():
                nonlocal active
                active += 1
                seen.append((i, active))
                time.sleep(0.02)
                active -= 1
                return SearchResult(OK, detail=str(i))
            return call

        calls = [lambda: SearchResult(BLOCKED), ok(1), lambda: 1 / 0] + [ok(i) for i in range(3, 7)]
        results, limit = http.run_parallel(calls, 2)
        self.assertEqual([r.status for r in results], [BLOCKED, OK, BROKEN] + [OK] * 4)
        self.assertEqual([r.detail for r in results[3:]], ["3", "4", "5", "6"])
        self.assertEqual(results[2].detail, "crash: ZeroDivisionError")
        self.assertEqual(limit, 1)
        self.assertTrue(all(n == 1 for i, n in seen if i >= 3))  # one at a time once halved


class FakeLefrecce:
    """Serves captured solutions like the real API: from body.departureTime onwards, 10 per offset."""

    def __init__(self, solutions):
        self.solutions, self.bodies = solutions, []

    def __call__(self, method, url, body=None, headers=None, opener=None):
        self.bodies.append(copy.deepcopy(body))
        start, off = body["departureTime"][:16], body["criteria"]["offset"]
        sols = [s for s in self.solutions if s["solution"]["departureTime"][:16] >= start]
        return 200, {"solutions": sols[off:off + 10]}


class TrenitaliaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pages = [fixture(f"trenitalia_day_p{i}.json.gz") for i in range(5)]
        cls.day = [s for p in cls.pages for s in p["solutions"]]

    def setUp(self):
        patcher = mock.patch("time.sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def search(self, response, **kw):
        fake = response if callable(response) else mock.Mock(return_value=response)
        with mock.patch.object(http, "request", fake):
            return trenitalia.search(MI, RM, DAY, **kw)

    def test_parser_on_real_page(self):
        raw = self.pages[0]["solutions"]
        offers, past_end = trenitalia.parse(raw, DAY)
        saleable = [o for s in raw for g in s["grids"] for sv in g["services"] for o in sv["offers"]
                    if o["status"] == "SALEABLE"]
        self.assertFalse(past_end)
        self.assertEqual(len(offers), len(saleable))
        self.assertTrue(any(o["status"] == "SOLD_OUT" for s in raw for g in s["grids"] for sv in g["services"]
                            for o in sv["offers"]))  # the fixture does exercise the sold-out filter
        self.assertEqual(len(trains(offers)), 10)
        self.assertLessEqual({o.cls for o in offers}, {"Standard", "Premium", "Business", "Business Area Silenzio",
                                                       "Business Salottino", "Executive"})
        self.assertLessEqual({"Base", "Economy", "Super Economy", "FrecciaYOUNG"}, {o.fare for o in offers})
        o = offers[0]
        self.assertEqual((o.operator, o.category, o.origin, o.destination), ("trenitalia", "FR", "Milano Centrale",
                                                                              "Roma Termini"))
        self.assertRegex(o.dep, r"^2026-11-10T\d\d:\d\d$")
        self.assertTrue(all(o.price > 0 and o.seats > 0 and not o.same_day_ar for o in offers))

    def test_full_day_pages_until_short_page(self):
        fake = FakeLefrecce(self.day)
        res = self.search(fake)
        self.assertEqual((res.status, res.requests, len(trains(res.offers))), (OK, 5, 46))
        self.assertEqual([b["criteria"]["offset"] for b in fake.bodies], [0, 10, 20, 30, 40])
        self.assertNotIn("returnDepartureTime", fake.bodies[0])
        self.assertTrue(fake.bodies[0]["criteria"]["frecceOnly"] and fake.bodies[0]["criteria"]["noChanges"])

    def test_full_page_then_empty_page_ends_the_day(self):
        res = self.search(FakeLefrecce(self.day[:40]))
        self.assertEqual((res.status, res.requests, len(trains(res.offers))), (OK, 5, 40))

    def test_window_stops_paging_past_its_end(self):
        fake = FakeLefrecce(self.day)
        res = self.search(fake, window=("08:00", "11:00"))
        want = {s["solution"]["trains"][0]["name"] for s in self.day
                if "08:00" <= s["solution"]["departureTime"][11:16] <= "11:00"}
        self.assertEqual({o.train for o in res.offers}, want)
        self.assertEqual(fake.bodies[0]["departureTime"], "2026-11-10T08:00:00.000")
        self.assertLessEqual(res.requests, 2)

    def test_no_solutions_is_empty(self):
        res = self.search((400, fixture("trenitalia_no_solutions.json.gz")))
        self.assertEqual((res.status, res.requests, res.offers), (EMPTY, 1, []))

    def test_all_sold_out_is_empty(self):
        sol = copy.deepcopy(self.day[0])
        for g in sol["grids"]:
            for sv in g["services"]:
                for o in sv["offers"]:
                    o["status"], o["price"] = "SOLD_OUT", None
        self.assertEqual(self.search((200, {"solutions": [sol]})).status, EMPTY)

    def test_failures_are_classified_with_log_safe_details(self):
        cases = [((400, fixture("trenitalia_bad_station.json.gz")), BROKEN), ((403, "<html>denied</html>"), BLOCKED),
                 ((429, ""), BLOCKED), ((200, "<html><script>challenge</script></html>"), BLOCKED),
                 ((502, "bad gateway"), BROKEN), ((None, "TimeoutError"), BROKEN),
                 ((200, {"solutions": [{"unexpected": 1}]}), BROKEN), ((200, {"error": "?"}), BROKEN)]
        for response, want in cases:
            res = self.search(response)
            self.assertEqual((res.status, res.offers), (want, []), response)
            for secret in (str(MI), str(RM), DAY, "Milano", "Roma", "€"):
                self.assertNotIn(secret, res.detail)

    def test_broken_page_drops_partial_day(self):
        fake = mock.Mock(side_effect=[(200, {"solutions": self.day[:10]}), (500, "")])
        res = self.search(fake)
        self.assertEqual((res.status, res.detail, res.requests, res.offers), (BROKEN, "http 500", 2, []))

    def test_same_day_round_trip(self):
        out_raw, ret_raw = fixture("trenitalia_ar_out.json.gz"), fixture("trenitalia_ar_return.json.gz")
        fake = mock.Mock(side_effect=[(200, out_raw), (200, ret_raw)])
        with mock.patch.object(http, "request", fake):
            out, ret = trenitalia.same_day_round_trip(MI, RM, DAY, ("07:00", "09:30"), ("18:00", "21:00"))
        (_, _, out_body), _ = fake.call_args_list[0]
        (_, _, ret_body), _ = fake.call_args_list[1]
        self.assertEqual(out_body["returnDepartureTime"], "2026-11-10T18:00:00.000")
        self.assertEqual((ret_body["departureLocationId"], ret_body["arrivalLocationId"]), (RM, MI))
        self.assertEqual(ret_body["returnDepartureTime"], "2026-11-10T23:59:00.000")
        for res, n in ((out, 8), (ret, 7)):  # return page also holds 3 next-day trains: dropped, paging stops
            self.assertEqual((res.status, res.requests, len(trains(res.offers))), (OK, 1, n))
            ar = [o for o in res.offers if o.same_day_ar]
            self.assertTrue(ar)
            self.assertEqual({o.fare for o in ar}, {"A/R IN GIORNATA"})
            self.assertIn("Base", {o.fare for o in res.offers})
        self.assertEqual({o.origin for o in ret.offers}, {"Roma Termini"})


def token_cookie():
    return Cookie(0, "BIGSessionToken", "test-jwt", None, False, ".italotreno.com", True, True, "/", True, True,
                  None, True, None, None, {})


class FakeItalo:
    """Scripted Italo API. `script` maps a route to (status, body) answers used before the happy defaults."""

    def __init__(self, session, result, **script):
        self.session, self.result, self.script, self.calls = session, result, script, []

    def __call__(self, method, url, body=None, headers=None, opener=None):
        route = ("login" if url == italo.LOGIN else "ws" if url.endswith("/working-sessions")
                 else "book" if method == "POST" else "delete" if method == "DELETE"
                 else "delete_status" if "/delete/status/" in url else "status")
        self.calls.append(route)
        if route not in ("login", "ws"):
            assert headers["Authorization"] == "Bearer test-jwt" and headers["X-BIG-working-session-id"]
        if self.script.get(route):
            return self.script[route].pop(0)
        if route == "login":
            self.session._jar.set_cookie(token_cookie())
        return {"login": (200, {"isAnonymous": True}), "ws": (200, {}),
                "book": (202, {"operationId": "op1", "pollAfter": 1500}), "status": (200, self.result),
                "delete": (202, {"operationId": "op2"}), "delete_status": (200, {"resultCode": 200})}[route]


class ItaloTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.day, cls.young, cls.rt = (fixture(f"italo_{n}.json.gz") for n in ("day", "young", "round_trip"))

    def setUp(self):
        patcher = mock.patch("time.sleep")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.session = italo.Session()

    def fake(self, result=None, **script):
        return FakeItalo(self.session, self.day if result is None else result, **script)

    def test_parser_on_real_day(self):
        trip = self.day["trips"][0]
        offers = italo.parse(trip, DAY)
        fares = [f for ts in trip["travelSolutions"] for j in ts["journeys"] for s in j["segments"] for f in s["fares"]]
        self.assertEqual(len(offers), len([f for f in fares if f["availableCount"]]))
        self.assertEqual(len(trains(offers)), 33)
        self.assertEqual({o.cls for o in offers}, {"Smart", "Prima", "Club", "Salotto"})
        self.assertLessEqual({"Flex", "Economy", "Low Cost"}, {o.fare for o in offers})
        o = offers[0]
        self.assertEqual((o.operator, o.category, o.origin, o.destination), ("italo", "IT", "MC_", "RMT"))
        self.assertTrue(all(o.price > 0 and not o.same_day_ar and o.dep.startswith(DAY) for o in offers))
        self.assertEqual(len(trains(italo.parse(trip, DAY, ("08:00", "11:00")))),
                         len({s["trainNumber"] for ts in trip["travelSolutions"] for j in ts["journeys"]
                              for s in j["segments"] if "08:00" <= s["std"][11:16] <= "11:00"}))

    def test_young_search_sees_the_young_fare(self):
        offers = italo.parse(self.young["trips"][0], DAY)
        self.assertIn("Italo Giovani 14-29 anni", {o.fare for o in offers})
        self.assertNotIn("Italo Giovani 14-29 anni", {o.fare for o in italo.parse(self.day["trips"][0], DAY)})

    def test_search_flow_reuses_login_and_working_session(self):
        fake = self.fake(status=[(202, {"operationId": "op1", "retryAfter": 1000})])
        with mock.patch.object(http, "request", fake):
            first = self.session.search("MC_", "RMT", DAY, passenger="young")
            second = self.session.search("MC_", "RMT", DAY, window=("08:00", "11:00"))
        self.assertEqual((first.status, len(trains(first.offers)), first.requests), (OK, 33, 7))
        self.assertEqual((second.status, second.requests), (OK, 4))  # no new login, no new working session
        self.assertEqual(fake.calls, ["login", "ws", "book", "status", "status", "delete", "delete_status",
                                      "book", "status", "delete", "delete_status"])
        self.assertTrue(second.offers and all("08:00" <= o.dep[11:] <= "11:00" for o in second.offers))

    def test_young_and_senior_bodies(self):
        self.assertEqual([italo._body("MC_", "RMT", DAY, None, p)[k] for p, k in
                          (("young", "youngPassengers"), ("senior", "seniorPassengers"), ("adult", "adultPassengers"))],
                         [1, 1, 1])
        body = italo._body("MC_", "RMT", DAY, DAY, "young")
        self.assertEqual((body["adultPassengers"], body["isRoundTrip"], body["returnDate"]), (0, True, DAY))

    def test_same_day_round_trip_marks_ar_on_both_directions(self):
        with mock.patch.object(http, "request", self.fake(self.rt)):
            fwd, bwd = self.session.round_trip("MC_", "RMT", DAY, DAY)
        self.assertEqual((fwd.status, bwd.status, bwd.requests), (OK, OK, 0))
        for res, origin in ((fwd, "MC_"), (bwd, "RMT")):
            ar = {o.fare for o in res.offers if o.same_day_ar}
            self.assertEqual(ar, {"Andata e Ritorno"})
            self.assertEqual({o.origin for o in res.offers}, {origin})
            self.assertEqual(len(trains(res.offers)), 33)

    def test_relogin_after_401(self):
        fake = self.fake(book=[(401, {"title": "Unauthorized"})])
        with mock.patch.object(http, "request", fake):
            res = self.session.search("MC_", "RMT", DAY)
        self.assertEqual(res.status, OK)
        self.assertEqual(fake.calls.count("login"), 2)

    def test_blocked_login(self):
        with mock.patch.object(http, "request", self.fake(login=[(403, "<html>denied</html>")])):
            res = self.session.search("MC_", "RMT", DAY)
        self.assertEqual((res.status, res.detail, res.offers), (BLOCKED, "http 403", []))

    def test_beyond_horizon_is_empty_and_keeps_the_working_session(self):
        fake = self.fake(status=[(404, fixture("italo_beyond_horizon.json.gz"))])
        with mock.patch.object(http, "request", fake):
            res = self.session.search("MC_", "RMT", "2027-06-01")
            again = self.session.search("MC_", "RMT", DAY)
        self.assertEqual((res.status, again.status), (EMPTY, OK))
        self.assertEqual(fake.calls.count("ws"), 1)
        self.assertEqual(fake.calls.count("delete"), 1)

    def test_failed_search_drops_its_working_session(self):
        fake = self.fake(status=[(500, {"type": "x", "title": "Dapr exception", "status": 500})])
        with mock.patch.object(http, "request", fake):
            res = self.session.search("ZZZ", "RMT", DAY)
            again = self.session.search("MC_", "RMT", DAY)
        self.assertEqual((res.status, res.detail, again.status), (BROKEN, "http 500", OK))
        self.assertEqual(fake.calls.count("ws"), 2)

    def test_schema_surprise_is_broken_but_booking_still_deleted(self):
        fake = self.fake({"bookingId": "b1", "trips": [{"direction": "forward", "travelSolutions": [{"x": 1}]}]})
        with mock.patch.object(http, "request", fake):
            res = self.session.search("MC_", "RMT", DAY)
        self.assertEqual((res.status, res.detail), (BROKEN, "schema: KeyError"))
        self.assertEqual(fake.calls.count("delete_status"), 1)

    def test_fifth_working_session_needs_a_new_login(self):
        fake = self.fake(status=[(500, "")] * 4)
        with mock.patch.object(http, "request", fake):
            for _ in range(5):
                self.session.search("MC_", "RMT", DAY)
        self.assertEqual((fake.calls.count("ws"), fake.calls.count("login")), (5, 2))


if __name__ == "__main__":
    unittest.main()
