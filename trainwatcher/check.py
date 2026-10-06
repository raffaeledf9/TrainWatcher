"""One Check: fetch the search units of the due Watches (shared, in parallel), record what came back, and build
each Watch's view (Lowest price, Ties, top 5, round-trip total) from the stored current offers."""
import dataclasses
from datetime import timedelta

from trainwatcher import prices, render, stations, store
from trainwatcher.offers import BROKEN, EMPTY, OK, Offer, SearchResult
from trainwatcher.operators import http, italo, trenitalia

CONCURRENCY = {"T": 5, "I": 3}
NAME = {"T": "trenitalia", "I": "italo"}
NA = "NA"  # the operator does not serve one of the stations
CANARY = ("milano-centrale", "roma-termini")  # always has trains


def record_units(watch, i):
    """Units whose stored offers feed leg i. A same-day round trip is searched once per operator; its two
    directions are stored under (op, A, B, day, pax, day) and (op, B, A, day, pax, day)."""
    leg, out = watch.legs[i], []
    for op in watch.operators:
        pax = watch.passenger if op == "I" else "adult"
        if watch.same_day:
            d = leg.dates()[0]
            out.append((op, leg.origin, leg.destination, d, pax, d))
        else:
            out += [(op, leg.origin, leg.destination, d, pax, None) for d in leg.dates()]
    return out


def fetch_units(watch):
    out = []
    for i in range(len(watch.legs)):
        for u in record_units(watch, i):
            if not (watch.same_day and i == 1) and u not in out:
                out.append(u)
    return out


def _call(u, session, ids):
    op, a, b, day, pax, ret = u
    x, y = ids[a], ids[b]
    if op == "T":
        if ret:
            out, back = trenitalia.same_day_round_trip(x, y, day)
            return dataclasses.replace(out, offers=out.offers), back
        return trenitalia.search(x, y, day), None
    if ret:
        return session.round_trip(x, y, day, ret, pax)
    return session.search(x, y, day, passenger=pax), None


def canary(op, session, today):
    """Decides 'really no trains' vs 'operator broken': a weekday about 5 weeks ahead on a route that always has trains."""
    day = today + timedelta(days=35)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    a, b = (stations.operator_ids(stations.by_key(k))[NAME[op]] for k in CANARY)
    try:
        r = trenitalia.search(a, b, day) if op == "T" else session.search(a, b, day)
    except Exception:
        return False
    return r.status == OK and bool(r.offers)


def fetch_and_record(db, units, now, session=None):
    """Fetch every unit once and store successful results. When an operator's results look suspicious (errors, or
    nothing but EMPTY) the canary decides; if it fails too, EMPTY counts as BROKEN and is not stored.
    Returns ({fetch_unit: status}, {failing operators})."""
    statuses, failing, session = {}, set(), session or italo.Session(workers=CONCURRENCY["I"])
    for op in ("T", "I"):
        todo = []
        for u in (u for u in units if u[0] == op):
            ids = {k: stations.operator_ids(stations.by_key(k)).get(NAME[op]) for k in (u[1], u[2])}
            if None in ids.values():
                statuses[u] = NA
                continue
            todo.append((u, ids))

        def wrap(u, ids):
            def call():
                try:
                    first, second = _call(u, session, ids)
                except Exception as e:  # a connector bug must not stop the whole run
                    first, second = SearchResult("BROKEN", detail=type(e).__name__), None
                first.pair = second
                return first
            return call

        results, _ = http.run_parallel([wrap(u, ids) for u, ids in todo], CONCURRENCY[op])
        if results and (any(r.status not in (OK, EMPTY) for r in results) or all(r.status == EMPTY for r in results))                 and not canary(op, session, now.date()):
            failing.add(op)
        for (u, _), res in zip(todo, results):
            statuses[u] = BROKEN if op in failing and res.status == EMPTY else res.status
            if statuses[u] in (OK, EMPTY):
                store.record(db, u, res.offers, now)
                back = getattr(res, "pair", None)
                if back is not None and back.status in (OK, EMPTY):
                    store.record(db, (u[0], u[2], u[1], u[3], u[4], u[5]), back.offers, now)
    return statuses, failing


def live_ok(watch, statuses):
    """True if every unit of this watch was searched successfully in this run (needed to claim 'gone')."""
    units = fetch_units(watch)
    return all(statuses.get(u) in (OK, EMPTY, NA) for u in units) and any(statuses.get(u) in (OK, EMPTY) for u in units)


def views(watch, db, top=5):
    """[view per leg], lowest (watch-level), legs ({out, ret} for round trips) from the stored current offers."""
    out = []
    for i, leg in enumerate(watch.legs):
        offers = [Offer(**d) for u in record_units(watch, i) for d in store.current_offers(db, u)]
        matching = [o for o in offers if prices.matches(o, watch, leg)]
        low, ties, others = prices.ranking(matching, top)
        v = {"low": low, "ties": ties, "others": others, "matching": matching, "other_fare": None}
        if low is None and watch.kind == "fare":  # status view only: the cheapest fare not being watched
            cheapest = dataclasses.replace(watch, fares=None)
            alt = [o for o in offers if prices.matches(o, cheapest, leg)]
            v["other_fare"] = min(alt, key=lambda o: o.price) if alt else None
        out.append(v)
    if not watch.round_trip:
        return out, out[0]["low"], None, None
    total = prices.round_trip_total(out[0]["matching"], out[1]["matching"])[0]
    return out, total, {"out": out[0]["low"], "ret": out[1]["low"]}, total


def chart_series(watch, db, views_, top=5):
    """[(legend, history)] for the trains currently shown (ties first), cheapest matching offer per train."""
    series = []
    for i, v in enumerate(views_):
        for o in (v["ties"] + v["others"])[:top]:
            for u in record_units(watch, i):
                k = store.offer_key(u, o)
                h = store.history(db, [k])[k]
                if h:
                    label = render.short_train(o) + "  " + render.times(o, watch.legs[i], arrow=" → ", html=False)
                    if len(views_) > 1:
                        label = ("→ " if i == 0 else "← ") + label
                    series.append((label, h))
                    break
    return series[:top * len(views_)]
