"""Italo connector: anonymous login, then one booking search per day (always deleted afterwards)."""
import threading
import time
import urllib.request
import uuid
from http.cookiejar import CookieJar

from . import http
from ..offers import BROKEN, EMPTY, ITALO, OK, Offer, SearchResult

LOGIN = "https://biglietti.italotreno.com/api/login"
API = "https://api-biglietti.italotreno.com/api/v1"
CLASSES = {"S": "Smart", "P": "Prima", "C": "Club", "S1": "Salotto", "S2": "Salotto"}  # S1 in round trips, S2 one-way
AR_FARE = "Andata e Ritorno"
PAX = {"adult": "adultPassengers", "young": "youngPassengers", "senior": "seniorPassengers"}
TOKEN_TTL = 50 * 60  # the session JWT lives 1 h; log in again before it can expire mid-search
WS_PER_LOGIN = 4     # a 5th working session on one login silently evicts the oldest
POLL_LIMIT = 30      # seconds; a garbage search stays pending ~20 s before failing
PAUSE = 0.5          # minimum seconds between polls (politeness)
SCHEMA_ERRORS = (KeyError, TypeError, ValueError, AttributeError, IndexError)


class _Fail(Exception):
    def __init__(self, verdict, detail):
        super().__init__(detail)
        self.verdict, self.detail = verdict, detail


def _fail(status, data):
    verdict, detail = http.classify(status, data)
    return _Fail(verdict, detail) if verdict != OK else _Fail(BROKEN, f"unexpected http {status}")


class Session:
    """One per run. Logs in lazily, keeps working sessions (each holds at most one open booking) and reuses them.
    Thread-safe: up to `workers` searches run at once, each on its own working session."""

    def __init__(self, workers=3):
        self._jar = CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._jar))
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(workers)
        self._free = []  # idle working sessions: (token, id, login time)
        self._token, self._born, self._made = None, 0.0, 0

    def search(self, origin, dest, day, window=None, passenger="adult"):
        """One-way Offers of direct trains departing on `day`; one call covers the whole day and
        window=("HH:MM", "HH:MM") filters departures. passenger: adult | young | senior."""
        t0 = time.monotonic()
        verdict, detail, data, n = self._run(_body(origin, dest, day, None, passenger))
        return _result(verdict, detail, data, "forward", str(day), window, n, t0)

    def round_trip(self, origin, dest, day, return_day, passenger="adult"):
        """(forward, backward) results of one round-trip search. When return_day == day both directions carry the
        "Andata e Ritorno" fare (same_day_ar=True). The request cost is booked on the forward result."""
        t0 = time.monotonic()
        verdict, detail, data, n = self._run(_body(origin, dest, day, return_day, passenger))
        return (_result(verdict, detail, data, "forward", str(day), None, n, t0),
                _result(verdict, detail, data, "backward", str(return_day), None, 0, t0))

    def _run(self, body):
        """One booking search, retried once on a fresh login after a 401. -> (verdict, detail, data, requests)."""
        spent = 0

        def call(method, url, payload=None, headers=None):
            nonlocal spent
            spent += 1
            return http.request(method, url, payload, headers, self._opener)

        for attempt in (0, 1):
            with self._slots:
                try:
                    ws = self._take(call)
                    verdict, detail, data, reusable = self._book(call, ws, body)
                    if reusable:
                        self._free.append(ws)
                except _Fail as f:
                    verdict, detail, data = f.verdict, f.detail, None
            if detail == "http 401" and not attempt:
                with self._lock:
                    self._token, self._free = None, []  # expired or revoked: log in again
                continue
            return verdict, detail, data, spent

    def _take(self, call):
        """An idle working session, else a new one (logging in first when needed)."""
        with self._lock:
            while self._free:
                ws = self._free.pop()
                if time.monotonic() - ws[2] < TOKEN_TTL:
                    return ws
            if not self._token or self._made >= WS_PER_LOGIN or time.monotonic() - self._born > TOKEN_TTL:
                self._jar.clear()
                status, data = call("POST", LOGIN, {"isAnonymous": True}, {"X-Anonymous-User": "true"})
                token = next((c.value for c in self._jar if c.name == "BIGSessionToken"), None)
                if status != 200 or not token:
                    raise _fail(status, data)
                self._token, self._born, self._made = token, time.monotonic(), 0
            ws = (self._token, str(uuid.uuid4()), self._born)
            status, data = call("POST", f"{API}/working-sessions", None, _auth(ws))
            if status != 200:
                raise _fail(status, data)
            self._made += 1
            return ws

    def _book(self, call, ws, body):
        """Create, poll and delete one booking. -> (verdict, detail, data, working session reusable)."""
        h = _auth(ws)
        status, data = call("POST", f"{API}/booking", body, h)
        if status != 202 or not isinstance(data, dict) or "operationId" not in data:
            raise _fail(status, data)
        op, wait = data["operationId"], data.get("pollAfter", 1000) / 1000
        deadline = time.monotonic() + POLL_LIMIT
        while True:
            time.sleep(min(max(wait, PAUSE), 2))
            status, data = call("GET", f"{API}/booking/status/{op}", headers=h)
            if status != 202:
                break
            if time.monotonic() > deadline:
                return BROKEN, "search still pending", None, False
            wait = data.get("retryAfter", 1000) / 1000 if isinstance(data, dict) else 1
        if status == 404 and isinstance(data, dict) and str(data.get("type")).endswith("/invalidtriprequest"):
            return EMPTY, "no trip offered", None, True  # e.g. past the booking horizon (maxDate)
        booking = data.get("bookingId") if status == 200 and isinstance(data, dict) else None
        if not booking:
            raise _fail(status, data)
        return OK, "", data, self._delete(call, h, booking)

    def _delete(self, call, h, booking):
        """Delete the open booking and wait until it is gone. -> True when confirmed."""
        status, data = call("DELETE", f"{API}/booking/{booking}", headers=h)
        if status != 202 or not isinstance(data, dict) or "operationId" not in data:
            return False
        op, deadline = data["operationId"], time.monotonic() + POLL_LIMIT
        while time.monotonic() < deadline:
            time.sleep(PAUSE)
            status, _ = call("GET", f"{API}/booking/{booking}/delete/status/{op}", headers=h)
            if status != 202:
                return status == 200
        return False


def _auth(ws):
    return {"Authorization": f"Bearer {ws[0]}", "X-BIG-working-session-id": ws[1]}


def _body(origin, dest, day, return_day, passenger):
    body = {"isRoundTrip": return_day is not None, "departureStation": origin, "arrivalStation": dest,
            "departureDate": str(day), "culture": "it-IT", "showPrivateOffers": False, "showBestPrices": True,
            "adultPassengers": 0, "youngPassengers": 0, "childPassengers": 0, "seniorPassengers": 0,
            "hasPet": False, "promoCode": "", "promocodeAlias": "", "employeeOffer": None, "passengersAges": None,
            "portalType": "B2C"}
    body[PAX[passenger]] = 1
    if return_day is not None:
        body["returnDate"] = str(return_day)
    return body


def _result(verdict, detail, data, direction, day, window, requests, t0):
    res = SearchResult(verdict, requests=requests, seconds=round(time.monotonic() - t0, 2), detail=detail)
    if verdict != OK:
        return res
    try:
        res.offers = [o for trip in data["trips"] if trip["direction"] == direction for o in parse(trip, day, window)]
    except SCHEMA_ERRORS as e:
        res.status, res.detail = BROKEN, f"schema: {type(e).__name__}"
        return res
    if not res.offers:
        res.status = EMPTY
    return res


def parse(trip, day, window=None):
    """Offers of the direct Italo trains of one trip (one direction) departing on `day`, within window if given.
    Station fields are Italo codes (e.g. MC_, RMT): the API reports no names."""
    start, end = window or ("00:00", "23:59")
    offers = []
    for sol in trip["travelSolutions"]:
        segments = [seg for j in sol["journeys"] for seg in j["segments"]]
        if len(segments) != 1 or sol["journeys"][0].get("serviceProvider", "ITALO") != "ITALO":
            continue  # connections and bus legs
        seg = segments[0]
        dep = seg["std"][:16]
        if dep[:10] != day or not start <= dep[11:] <= end:
            continue
        for f in seg["fares"]:
            if f.get("availableCount") == 0:
                continue
            offers.append(Offer(
                ITALO, seg["trainNumber"], "IT", dep, seg["sta"][:16], seg["departureStation"], seg["arrivalStation"],
                CLASSES.get(f["productClass"], f["productClass"]), f["offerType"],
                float(f["paxFares"][0]["singlePaxFarePrice"]), f.get("availableCount"), f["offerType"] == AR_FARE))
    return offers
