"""When each Watch is Checked and what one Run fetches (map decision "Run budget and check cadence")."""
from datetime import date, datetime, timedelta

RUN_FETCH_BUDGET_S = 180          # seconds of fetching per Run
RUNS_PER_HOUR = 12                # one Run every 5 minutes
CAPACITY_S_PER_HOUR = RUN_FETCH_BUDGET_S * RUNS_PER_HOUR
COST_S = {"T": 3.0, "I": 4.5}     # measured wall time per operator search unit, with parallelism (research 01/02/15)
WARN_LOAD = 0.70


def cadence_minutes(first_day, today):
    days = (first_day - today).days
    if days <= 3:
        return 15
    if days <= 14:
        return 30
    if days <= 45:
        return 120
    return 360


def next_check(watch, now):
    return now + timedelta(minutes=cadence_minutes(watch.first_day, now.date()))


def units(watch):
    """Search units a Check needs: (operator, origin, destination, day, passenger, return_day_or_None).
    Same-day round trips are one round-trip unit per operator; other legs are one unit per day."""
    out = []
    for op in watch.operators:
        pax = watch.passenger if op == "I" else "adult"  # Trenitalia shows young/senior fares in the adult search
        if watch.same_day:
            d = watch.legs[0].dates()[0]
            out.append((op, watch.legs[0].origin, watch.legs[0].destination, d, pax, d))
            continue
        for leg in watch.legs:
            for d in leg.dates():
                out.append((op, leg.origin, leg.destination, d, pax, None))
    return out


def plan(due_watches, now, budget_s=RUN_FETCH_BUDGET_S):
    """Order due watches (nearest departure first, then the longest-unchecked; a watch skipped last run goes
    first) and take them while the estimated cost fits the budget. Units shared by several watches are
    fetched once. Returns (watches_to_check, units_to_fetch, deferred_watches)."""
    order = sorted(due_watches, key=lambda w: (not getattr(w, "skipped", False), w.first_day, getattr(w, "last_check", None) or datetime.min))
    take, fetch, deferred, spent = [], {}, [], 0.0
    for w in order:
        new = [u for u in units(w) if u not in fetch]
        cost = sum(COST_S[u[0]] for u in new) / 5  # ~5 units in flight at once
        if take and spent + cost > budget_s:
            deferred.append(w)
            continue
        take.append(w)
        spent += cost
        for u in new:
            fetch[u] = True
    return take, list(fetch), deferred


def load(active_watches, today):
    """Estimated share of fetch capacity used per hour (0..1+). Above WARN_LOAD the bot warns the Owner."""
    per_hour = 0.0
    for w in active_watches:
        cost = sum(COST_S[u[0]] for u in units(w)) / 5
        per_hour += cost * 60 / cadence_minutes(w.first_day, today)
    return per_hour / CAPACITY_S_PER_HOUR


def is_past(watch, today):
    return watch.last_day < today
