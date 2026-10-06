"""Shared contract between operator connectors and the rest of the job.

A connector turns one search (route, day, optional window, passenger type) into a SearchResult:
a status plus a flat list of Offers. Glossary terms (Offer, Fare, Class, Train) follow CONTEXT.md.
"""
from dataclasses import dataclass, field

TRENITALIA, ITALO = "trenitalia", "italo"

# SearchResult.status values
OK = "OK"            # valid response with at least one train
EMPTY = "EMPTY"      # valid response, no trains (may be genuine; confirm with the canary)
BLOCKED = "BLOCKED"  # 403/429 or a bot-protection challenge
BROKEN = "BROKEN"    # unexpected format, 5xx, timeout


@dataclass(frozen=True)
class Offer:
    operator: str          # TRENITALIA | ITALO
    train: str             # train number, e.g. "9611"
    category: str          # "FR" | "FA" | "FB" | "IT"
    dep: str               # ISO local datetime "2026-11-10T08:00"
    arr: str               # ISO local datetime
    origin: str            # station name as the operator reports it
    destination: str
    cls: str               # Standard, Premium, Business, Executive, Smart, Prima, Club, Salotto
    fare: str              # Base, Economy, Super Economy, FrecciaYOUNG, Low Cost, A/R IN GIORNATA, ...
    price: float           # euro, per passenger, per leg
    seats: int | None = None   # seats left at this price if the operator says so
    same_day_ar: bool = False  # True for same-day return fares (valid only with both legs A/R)

    @property
    def key(self):
        """Identity of an offer across checks: what an Observation is keyed on."""
        return (self.operator, self.train, self.dep, self.cls, self.fare)


@dataclass
class SearchResult:
    status: str
    offers: list = field(default_factory=list)
    requests: int = 0       # HTTP requests spent (for the run budget)
    seconds: float = 0.0    # wall time
    detail: str = ""        # short, log-safe reason for non-OK statuses (no routes, prices or ids)
