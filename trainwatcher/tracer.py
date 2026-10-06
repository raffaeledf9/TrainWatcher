"""R1 tracer bullet (throwaway, replaced by trainwatcher.operators in R2): one hard-coded
Milano Centrale -> Roma Termini search on both operators, ~5 weeks ahead, cheapest price only."""
import gzip
import http.cookiejar
import json
import time
import urllib.request
import uuid
from datetime import date, timedelta

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"


def _call(opener, url, body=None, headers=None, method=None):
    h = {"User-Agent": UA, "Accept": "application/json", "Accept-Encoding": "gzip", **(headers or {})}
    data = None
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    with opener.open(urllib.request.Request(url, data=data, headers=h, method=method), timeout=60) as r:
        raw = r.read()
        raw = gzip.decompress(raw) if r.headers.get("Content-Encoding") == "gzip" else raw
        return r.status, (json.loads(raw) if raw.strip() else {})


def trenitalia(day):
    op = urllib.request.build_opener()
    _, d = _call(op, "https://www.lefrecce.it/Channels.Website.BFF.WEB/website/ticket/solutions", {
        "departureLocationId": 830001700, "arrivalLocationId": 830008409, "departureTime": f"{day}T06:00:00.000", "adults": 1, "children": 0,
        "criteria": {"frecceOnly": True, "regionalOnly": False, "noChanges": True, "order": "DEPARTURE_DATE", "limit": 10, "offset": 0},
        "advancedSearchRequest": {"bestFare": False}})
    prices = [o["price"]["amount"] for s in d["solutions"] for g in s.get("grids", []) for sv in g.get("services", [])
              for o in sv.get("offers", []) if (o.get("price") or {}).get("amount")]
    return min(prices), len(d["solutions"])


def italo(day):
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    _call(op, "https://biglietti.italotreno.com/api/login", {"isAnonymous": True}, {"X-Anonymous-User": "true"})
    H = {"Authorization": "Bearer " + next(c.value for c in jar if c.name == "BIGSessionToken"), "X-BIG-working-session-id": str(uuid.uuid4())}
    api = "https://api-biglietti.italotreno.com/api/v1"
    _call(op, f"{api}/working-sessions", None, H, "POST")
    _, r = _call(op, f"{api}/booking", {"isRoundTrip": False, "departureStation": "MC_", "arrivalStation": "RMT", "departureDate": str(day),
              "culture": "it-IT", "showPrivateOffers": False, "showBestPrices": True, "adultPassengers": 1, "youngPassengers": 0, "childPassengers": 0,
              "seniorPassengers": 0, "hasPet": False, "promoCode": "", "promocodeAlias": "", "employeeOffer": None, "passengersAges": None, "portalType": "B2C"}, H)
    for _ in range(30):
        time.sleep(1)
        s, d = _call(op, f"{api}/booking/status/{r['operationId']}", None, H)
        if s == 200:
            break
    _call(op, f"{api}/booking/{d['bookingId']}", None, H, "DELETE")
    sols = d["trips"][0]["travelSolutions"]
    prices = [f["paxFares"][0]["singlePaxFarePrice"] for s in sols for j in s["journeys"] for g in j["segments"] for f in g["fares"]]
    return min(prices), len(sols)


def report():
    day = date.today() + timedelta(days=35)
    lines = [f"🧪 <b>Tracer search</b> · Milano C.le → Roma Termini · {day:%a %d %b}"]
    for name, fn in (("Trenitalia", trenitalia), ("Italo", italo)):
        try:
            low, n = fn(day)
            lines.append(f"{name}: lowest <b>€{low:.2f}</b> ({n} trains checked)")
        except Exception as e:  # tracer only: report the failure kind, not details
            lines.append(f"{name}: failed ({type(e).__name__})")
    return "\n".join(lines)
