"""Alert rules (map decision "Watch and alert semantics" + Owner rules from the message prototype).

evaluate() compares one Check's outcome with the Watch's alert state and returns the Alert events to send
(rendered together as one message per Watch) plus the new state. The watch-level number is the Lowest
price: the minimum Best price over all its days (multi-day), or the total (round trip).
"""
from datetime import datetime, time

# Purchase windows: days before departure after which a fare can no longer be bought.
WINDOW_DAYS = {"T:FrecciaYOUNG": 11, "T:FrecciaSENIOR": 11, "I:Italo Giovani": 11, "I:Italo Senior": 11, "TI:A/R same day": 3}
NIGHT = (time(23, 0), time(7, 0))  # silent notifications in Europe/Rome local time


def window_days(watch):
    """Shortest purchase window among the tracked fares (Fare watches only), else None."""
    days = [WINDOW_DAYS[f] for f in (watch.fares or []) if f in WINDOW_DAYS]
    return min(days) if days else None


def evaluate(watch, state, lowest, live_ok, today, legs=None, low_day=None):
    """watch: Watch; state: dict from the last Check ({} on the first); lowest: float|None (None = no matching
    offer); live_ok: True if every search unit of this Check succeeded (needed to claim "gone");
    legs: optional {"out": price, "ret": price} for round trips; low_day: travel day of the cheapest train.
    Returns (events, new_state).

    Purchase windows are per travel day: a multi-day watch's cheapest day decides "last day to buy" and "still on
    sale", and the fare is gone because of the window only once the last day's window has closed."""
    s = dict(state)
    prev, events = s.get("low"), []
    mx = watch.max_price
    low_day = low_day or watch.first_day
    wdays = window_days(watch)
    days_left = (low_day - today).days
    window_closed = wdays is not None and (watch.last_day - today).days < wdays

    def ev(kind, **data):
        e = {"kind": kind, "low": lowest, "prev": prev, "legs": legs, "prev_legs": s.get("legs"), "low_day": low_day}
        if mx is not None and lowest is not None:
            e["max"], e["under_max"] = mx, lowest <= mx
        e.update(data)
        events.append(e)

    if "low" not in s:                                   # first Check: the status view is the baseline
        ev("baseline")
        s["alerted_low"] = lowest
        s["armed"] = not (mx is not None and lowest is not None and lowest <= mx)
    elif lowest is None:
        if prev is not None and live_ok:                 # gone only after a live Check confirmed it everywhere
            ev("gone", reason="window" if window_closed else "sold_out")
        # a failed Check proves nothing: keep the previous state and stay silent
    elif prev is None:                                   # "back" only if it was ever on sale (else: sales just opened)
        ev("back" if s.get("alerted_low") is not None else "on_sale")
        s["alerted_low"] = lowest
    elif watch.kind == "fare":
        if lowest != prev:                               # every change, any amount; max only marks it
            ev("rise" if lowest > prev else "drop")
    else:                                                # Cheapest watch
        alerted = s.get("alerted_low")
        if watch.rises:                                  # switched on: every change, drops still gated by max
            if lowest > prev:
                ev("rise")
            elif lowest < prev and (mx is None or lowest <= mx):
                ev("drop")
        elif (alerted is None or lowest < alerted) and (mx is None or lowest <= mx):
            ev("drop")                                   # only below the lowest already alerted
        if any(e["kind"] == "drop" for e in events):
            s["alerted_low"] = lowest if alerted is None else min(alerted, lowest)
        if mx is not None:
            if lowest <= mx and s.get("armed", True):
                if not any(e["kind"] == "drop" for e in events):
                    ev("under_max")
                s["armed"] = False
            elif lowest > mx:
                s["armed"] = True                        # re-arm once the price goes back above max

    if lowest is not None and wdays is not None:
        if days_left < wdays and s.get("still_sent") != str(low_day):
            ev("still_on_sale")
            s["still_sent"] = str(low_day)
        if days_left == wdays and s.get("last_day_sent") != str(today):
            ev("last_day")
            s["last_day_sent"] = str(today)

    if lowest is not None or live_ok:
        s["low"] = lowest
        s["legs"] = legs
    return events, s


def silent(now_local: datetime):
    t = now_local.time()
    return t >= NIGHT[0] or t < NIGHT[1]
