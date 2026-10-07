"""Trenitalia (lefrecce.it) connector: direct Frecce trains of one day, 10 solutions per request."""
import time

from . import http
from ..offers import BROKEN, EMPTY, OK, TRENITALIA, Offer, SearchResult

URL = "https://www.lefrecce.it/Channels.Website.BFF.WEB/website/ticket/solutions"
PAGE = 10        # the API never returns more per request, whatever `limit` says
MAX_PAGES = 12   # ponytail: hard stop in case offset paging ever stops advancing; busiest day seen = 5 pages
PAUSE = 0.5      # seconds between sequential requests (politeness)
LATE = "23:59"
AR_FARE = "A/R IN GIORNATA"
FARES = {"BASE": "Base"}  # the API shouts this one only
SCHEMA_ERRORS = (KeyError, TypeError, ValueError, AttributeError, IndexError)


def search(origin_id, dest_id, day, window=None, ar_return=None):
    """Offers of the direct Frecce departing on `day` ("YYYY-MM-DD" or a date), optionally only within
    window=("HH:MM", "HH:MM"). Pages until a short page or a departure past the window or the day.

    ar_return="HH:MM" sends a same-day returnDepartureTime: "A/R IN GIORNATA" offers then appear too,
    marked same_day_ar=True. Status EMPTY = valid answer with nothing on sale (no trains, or all sold out)."""
    day, (start, end) = str(day), window or ("00:00", LATE)
    t0, res = time.monotonic(), SearchResult(OK)
    body = {"departureLocationId": int(origin_id), "arrivalLocationId": int(dest_id),
            "departureTime": f"{day}T{start}:00.000", "adults": 1, "children": 0,
            "criteria": {"frecceOnly": True, "regionalOnly": False, "noChanges": True,
                         "order": "DEPARTURE_DATE", "limit": PAGE, "offset": 0},
            "advancedSearchRequest": {"bestFare": False}}
    if ar_return:
        body["returnDepartureTime"] = f"{day}T{ar_return}:00.000"
    for page in range(MAX_PAGES):
        if page:
            time.sleep(PAUSE)
        body["criteria"]["offset"] = page * PAGE
        status, data = http.request("POST", URL, body)
        res.requests += 1
        if status == 400 and isinstance(data, dict) and data.get("silent") is True:
            break  # "no travel solutions with the selected search criteria" arrives as a silent 400
        verdict, detail = http.classify(status, data)
        if verdict != OK:
            res.status, res.detail = verdict, detail
            break
        try:
            solutions = data["solutions"]
            offers, past_end = parse(solutions, day, start, end)
        except SCHEMA_ERRORS as e:
            res.status, res.detail = BROKEN, f"schema: {type(e).__name__}"
            break
        res.offers += offers
        if past_end or len(solutions) < PAGE:
            break
    if res.status != OK:
        res.offers = []  # never hand out a partial day as if it were complete
    elif not res.offers:
        res.status = EMPTY
    res.seconds = round(time.monotonic() - t0, 2)
    return res


def same_day_round_trip(origin_id, dest_id, day, out_window=None, ret_window=None):
    """Same-day A/R: (outbound result, return result), both with "A/R IN GIORNATA" offers marked same_day_ar.

    The return leg is priced by a reverse-direction search with a later same-day return time: its A/R price is
    the official per-leg rule (0.7 x Base, 0.5 on Saturdays), valid only if both legs are bought A/R together."""
    out = search(origin_id, dest_id, day, out_window, ar_return=ret_window[0] if ret_window else LATE)
    time.sleep(PAUSE)
    return out, search(dest_id, origin_id, day, ret_window, ar_return=LATE)


def parse(solutions, day, start="00:00", end=LATE):
    """-> (saleable Offers of single-train solutions departing on `day` between start and end,
    True once a solution lies past the window or on the next day: later pages only go further)."""
    offers, past_end = [], False
    for s in solutions:
        sol = s["solution"]
        dep = sol["departureTime"][:16]
        if dep[:10] != day or dep[11:] > end:
            past_end = True  # a late start spills into the next day even with offset paging
            continue
        if dep[11:] < start or len(sol["trains"]) != 1:
            continue
        train = sol["trains"][0]
        for grid in s.get("grids") or ():
            for service in grid["services"]:
                for o in service["offers"]:
                    if o.get("status") != "SALEABLE" or not o.get("price") or float(o["price"]["amount"]) <= 0:
                        continue  # a price of 0 would be a data glitch, and a false "new low" alert
                    offers.append(Offer(
                        TRENITALIA, train["name"], train["acronym"], dep, sol["arrivalTime"][:16],
                        sol["origin"], sol["destination"], service["name"].title(), FARES.get(o["name"], o["name"]),
                        float(o["price"]["amount"]), o.get("availableAmount"), o["name"] == AR_FARE))
    return offers, past_end
