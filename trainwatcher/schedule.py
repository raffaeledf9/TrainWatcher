"""When each Watch is Checked and what one Run fetches (map decision "Run budget and check cadence")."""
from datetime import datetime, timedelta

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


def next_check(watch, now, today=None):
    today = today or now.date()
    return now + timedelta(minutes=cadence_minutes(watch.next_day(today), today))


def units(watch, since=None):
    """Search units a Check needs (the same ones check.fetch_units fetches)."""
    from trainwatcher.check import fetch_units
    return fetch_units(watch, since)


def plan(due_watches, now, budget_s=RUN_FETCH_BUDGET_S, today=None, first=()):
    """Order due watches (nearest departure first, then the longest-unchecked; watches skipped by earlier runs go
    first, longest-unchecked first) and take them while the estimated cost fits the budget. Units shared by several
    watches are fetched once. Returns (watches_to_check, units_to_fetch, deferred_watches)."""
    today = today or now.date()

    def key(w):
        # first: watches the Owner just created or asked to check now; never postpone what they're waiting for
        waited = getattr(w, "last_check", None) or datetime.min
        if getattr(w, "skipped", False):  # by departure, each run's new leftovers would overtake older ones forever
            return (w.id not in first, 0, waited, w.next_day(today))
        return (w.id not in first, 1, w.next_day(today), waited)
    order = sorted(due_watches, key=key)
    take, fetch, deferred, spent = [], {}, [], 0.0
    for w in order:
        new = [u for u in units(w, today) if u not in fetch]
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
        cost = sum(COST_S[u[0]] for u in units(w, today)) / 5
        per_hour += cost * 60 / cadence_minutes(w.next_day(today), today)
    return per_hour / CAPACITY_S_PER_HOUR


def is_past(watch, today):
    return watch.last_day < today
