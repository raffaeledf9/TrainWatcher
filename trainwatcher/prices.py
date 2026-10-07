"""From Offers to what the Owner sees: which offers match a Watch, Best price per Train, Lowest price
and Ties per Leg, round-trip totals (CONTEXT.md)."""
import bisect

from trainwatcher.model import FARES
from trainwatcher.offers import TRENITALIA

# Operator fare names -> the form's fare ids. Unknown fares (e.g. a new promo) still count for Cheapest watches.
_FARE_ALIASES = {
    ("T", "base"): "T:Base", ("T", "economy"): "T:Economy", ("T", "super economy"): "T:Super Economy",
    ("T", "frecciayoung"): "T:FrecciaYOUNG", ("T", "frecciasenior"): "T:FrecciaSENIOR",
    ("I", "flex"): "I:Flex", ("I", "economy"): "I:Economy", ("I", "low cost"): "I:Low Cost", ("I", "extra magic"): "I:eXtra Magic",
}


def fare_id(offer):
    if offer.same_day_ar:
        return "TI:A/R same day"
    op = "T" if offer.operator == TRENITALIA else "I"
    name = offer.fare.strip().lower()
    if op == "I" and name.startswith("italo giovani"):
        return "I:Italo Giovani"
    if op == "I" and name.startswith("italo senior"):
        return "I:Italo Senior"
    return _FARE_ALIASES.get((op, name))


def matches(offer, watch, leg):
    op = "T" if offer.operator == TRENITALIA else "I"
    if op not in watch.operators or not leg.accepts(offer.dep):
        return False
    if watch.classes and not any(offer.cls == c or offer.cls.startswith(c + " ") for c in watch.classes):
        return False
    fid = fare_id(offer)
    if offer.same_day_ar and not watch.same_day:
        return False                      # A/R fares exist only for same-day round trips
    if fid and FARES.get(fid) not in (None, watch.passenger):
        return False                      # e.g. FrecciaYOUNG is not for an adult
    if watch.fares:
        return fid in watch.fares         # Fare watch: only the chosen fares, no fallback
    return True                           # Cheapest watch: everything the passenger can buy


def train_key(offer):
    return (offer.operator, offer.train, offer.dep)


def best_per_train(offers):
    """{train_key: cheapest matching Offer}; offers must already be filtered with matches()."""
    best = {}
    for o in offers:
        k = train_key(o)
        if k not in best or o.price < best[k].price:
            best[k] = o
    return best


def ranking(offers, top=5):
    """(lowest, ties, others): every Train at the Lowest price, then the next cheapest up to `top` in total
    (ties are never cut, so the list may be longer than `top`)."""
    best = sorted(best_per_train(offers).values(), key=lambda o: (o.price, o.dep))
    if not best:
        return None, [], []
    low = best[0].price
    ties = [o for o in best if o.price == low]
    others = [o for o in best if o.price != low][:max(0, top - len(ties))]
    return low, ties, others


def round_trip_total(out_offers, ret_offers):
    """Cheapest total for a round trip that can really be travelled: the return leaves after the outbound
    arrives, and it's one-way + one-way or A/R + A/R of the same operator (A/R fares are bought together).
    Returns (total, out_offer, ret_offer) or (None, None, None)."""
    best = (None, None, None)
    families = [(False, None)] + [(True, op) for op in sorted({o.operator for o in out_offers if o.same_day_ar})]
    for ar, op in families:
        pick = lambda offers: [o for o in offers if o.same_day_ar == ar and op in (None, o.operator)]
        rets = sorted(pick(ret_offers), key=lambda o: o.dep)
        cheapest_from = [None] * (len(rets) + 1)             # cheapest return among rets[i:]
        for i in range(len(rets) - 1, -1, -1):
            nxt = cheapest_from[i + 1]
            cheapest_from[i] = rets[i] if nxt is None or rets[i].price < nxt.price else nxt
        deps = [o.dep for o in rets]
        for a in pick(out_offers):
            b = cheapest_from[bisect.bisect_right(deps, a.arr)]
            if b is not None and (best[0] is None or a.price + b.price < best[0]):
                best = (round(a.price + b.price, 2), a, b)
    return best
