"""Live smoke test: one canary search per operator. Prints only status, counts and timing (public CI logs).

    python -m trainwatcher.smoke     # exit code 0 only when both operators answer OK
"""
import datetime
import sys

from .offers import OK
from .operators import italo, trenitalia

# Canary route Milano Centrale -> Roma Termini: always has trains on both operators.
TRENITALIA_IDS = (830001700, 830008409)
ITALO_CODES = ("MC_", "RMT")


def canary_day(today=None):
    """A weekday about five weeks ahead."""
    day = (today or datetime.date.today()) + datetime.timedelta(weeks=5)
    return day + datetime.timedelta(days=7 - day.weekday()) if day.weekday() >= 5 else day


def main():
    day = canary_day()
    results = {"trenitalia": trenitalia.search(*TRENITALIA_IDS, day),
               "italo": italo.Session(workers=1).search(*ITALO_CODES, day)}
    for name, r in results.items():
        trains = len({(o.train, o.dep) for o in r.offers})
        print(f"{name}: {r.status} trains={trains} offers={len(r.offers)} requests={r.requests} "
              f"seconds={r.seconds:.1f}" + (f" detail={r.detail}" if r.detail else ""))
    return 0 if all(r.status == OK for r in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
