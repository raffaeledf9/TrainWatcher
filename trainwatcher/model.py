"""Watches and Legs (CONTEXT.md). A Watch is built from the Mini App payload and validated here,
because the payload comes from the client and "a bad client can send arbitrary data" (Telegram docs)."""
import calendar
from dataclasses import dataclass, field, asdict
from datetime import date, time, timedelta

PASSENGERS = ("adult", "young", "senior")
OPERATORS = ("T", "I")
CLASSES = ("Standard", "Premium", "Business", "Executive", "Smart", "Prima", "Club", "Salotto")
# Fare ids as the form sends them: "<operators>:<label>"; the optional third item restricts the passenger type.
FARES = {
    "T:Base": None, "T:Economy": None, "T:Super Economy": None, "T:FrecciaYOUNG": "young", "T:FrecciaSENIOR": "senior",
    "I:Flex": None, "I:Economy": None, "I:Low Cost": None, "I:eXtra Magic": None, "I:Italo Giovani": "young", "I:Italo Senior": "senior",
    "TI:A/R same day": None,
}
HORIZON_DAYS = 200  # beyond any operator's booking horizon; days past the real horizon simply return no trains


class Invalid(ValueError):
    """The payload cannot become a Watch; the message is shown to the Owner."""


@dataclass
class Leg:
    origin: str                 # station key (webapp/stations.json "k")
    destination: str
    days: list                  # ["2026-11-10"] | ["2026-11-10", "2026-11-16"] (range) | ["2026-11"] (whole month)
    window: list | None = None  # ["06:00", "12:00"]: departures in [start, end]

    def dates(self):
        if len(self.days) == 1 and len(self.days[0]) == 7:
            y, m = map(int, self.days[0].split("-"))
            return [date(y, m, d) for d in range(1, calendar.monthrange(y, m)[1] + 1)]
        a = date.fromisoformat(self.days[0])
        b = date.fromisoformat(self.days[-1])
        return [a + timedelta(days=i) for i in range((b - a).days + 1)]

    def accepts(self, dep_iso):
        """True if a departure (ISO local datetime) falls on one of the leg's days and inside its window."""
        d, t = date.fromisoformat(dep_iso[:10]), time.fromisoformat(dep_iso[11:16])
        if d not in self.dates():
            return False
        if not self.window:
            return True
        return time.fromisoformat(self.window[0]) <= t <= time.fromisoformat(self.window[1])


@dataclass
class Watch:
    user_id: int
    legs: list
    passenger: str = "adult"
    fares: list | None = None        # None = Cheapest watch; else Fare watch on these fare ids
    classes: list | None = None
    operators: list = field(default_factory=lambda: list(OPERATORS))
    max_price: float | None = None   # per leg (one-way) or total (round trip)
    rises: bool = False              # Cheapest watches only; Fare watches always alert on rises
    id: int | None = None
    status: str = "active"           # active | past

    @property
    def kind(self):
        return "fare" if self.fares else "cheapest"

    @property
    def round_trip(self):
        return len(self.legs) == 2

    @property
    def first_day(self):
        return min(self.legs[0].dates())

    @property
    def last_day(self):
        return max(d for leg in self.legs for d in leg.dates())

    @property
    def same_day(self):
        return self.round_trip and self.legs[0].dates() == self.legs[1].dates() and len(self.legs[0].dates()) == 1

    def to_json(self):
        return asdict(self)

    @staticmethod
    def from_json(d):
        d = dict(d)
        d["legs"] = [Leg(**leg) for leg in d["legs"]]
        return Watch(**d)


def from_payload(p, user_id, station_keys, today=None):
    """Build and validate a Watch from the Mini App payload (see the form's payload())."""
    today = today or date.today()
    if not isinstance(p, dict) or p.get("v") != 1:
        raise Invalid("unsupported form version")
    for k in ("from", "to"):
        if p.get(k) not in station_keys:
            raise Invalid("unknown station")
    if p["from"] == p["to"]:
        raise Invalid("origin and destination are the same")

    def leg(raw, a, b):
        if not isinstance(raw, dict) or not isinstance(raw.get("d"), list) or not 1 <= len(raw["d"]) <= 2:
            raise Invalid("bad dates")
        w = raw.get("w")
        if w is not None:
            if not (isinstance(w, list) and len(w) == 2):
                raise Invalid("bad time window")
            try:
                if time.fromisoformat(w[0]) > time.fromisoformat(w[1]):
                    raise Invalid("time window ends before it starts")
            except ValueError:
                raise Invalid("bad time window")
        try:
            lg = Leg(a, b, [str(x) for x in raw["d"]], w)
            ds = lg.dates()
        except ValueError:
            raise Invalid("bad dates")
        if not ds or max(ds) < today:
            raise Invalid("dates are in the past")
        if min(ds) > today + timedelta(days=HORIZON_DAYS) or len(ds) > 31:
            raise Invalid("dates too far or too many")
        return lg

    legs = [leg(p.get("out"), p["from"], p["to"])]
    if p.get("ret") is not None:
        legs.append(leg(p["ret"], p["to"], p["from"]))
        if max(legs[1].dates()) < min(legs[0].dates()):
            raise Invalid("return before outbound")
    pax = p.get("pax", "adult")
    if pax not in PASSENGERS:
        raise Invalid("bad passenger type")
    fares = p.get("fares")
    if fares is not None:
        if not isinstance(fares, list) or not fares or any(f not in FARES for f in fares):
            raise Invalid("unknown fare")
        if any(FARES[f] not in (None, pax) for f in fares):
            raise Invalid("fare not available for this passenger type")
    classes = p.get("cls")
    if classes is not None and (not isinstance(classes, list) or any(c not in CLASSES for c in classes)):
        raise Invalid("unknown class")
    ops = p.get("ops", list(OPERATORS))
    if not isinstance(ops, list) or not ops or any(o not in OPERATORS for o in ops):
        raise Invalid("bad operators")
    mx = p.get("max")
    if mx is not None and (not isinstance(mx, (int, float)) or not 0 < mx < 10000):
        raise Invalid("bad max price")
    return Watch(user_id=user_id, legs=legs, passenger=pax, fares=fares, classes=classes, operators=ops,
                 max_price=float(mx) if mx is not None else None, rises=bool(p.get("rises")) and fares is None)
