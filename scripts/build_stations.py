"""Build webapp/stations.json, the station catalogue used by the Mini App and the resolver.

Sources (downloaded once into .cache/; delete a file there to refresh it):
- Trenitalia GTFS (CC-BY-4.0, official NeTEx via deryclem/trenitalia-gtfs): stops of FR/FA/FB trips,
  whose StopPlace id suffix is the lefrecce location id, with coordinates;
- trenitalia.com cruscotto-stations.json (first party): lefrecce spelling and Frecce flags;
- Italo /api/v1/stations (anonymous session): Italo codes, city groups (MAC) and coordinates.

Entry: {"k": key, "n": name, "t": lefrecce id|null, "i": Italo code|null, "g": 1 if city group, "a": abbr}.

    python scripts/build_stations.py
"""
import csv
import http.cookiejar
import io
import json
import math
import re
import unicodedata
import urllib.request
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache"
OUT = ROOT / "webapp" / "stations.json"
UA = {"User-Agent": "Mozilla/5.0 (TrainWatcher station catalogue builder)"}
CRUSCOTTO = "https://www.trenitalia.com/content/trenitalia/it.cruscotto-stations.json"
GTFS = "https://github.com/deryclem/trenitalia-gtfs/raw/main/gtfs-trenitalia.zip"
MAX_KM = 0.5  # an Italo station this close to a Frecce stop is the same station

# Trenitalia multistation ids "( Tutte Le Stazioni )", checked with lefrecce locations/search on
# 2026-10-06. Cities with >= 2 Frecce stations, plus Bologna, Bari and Reggio Calabria (whole-city
# searches). Italo MACs (MI0, RM0, NA0, VE0) are merged in by city name.
GROUPS = {
    "Milano": 830001650, "Roma": 830008349, "Napoli": 830009993, "Venezia": 830002998,
    "Torino": 830000996, "Firenze": 830006998, "Bologna": 830005999, "Genova": 830004999,
    "Bari": 830011699, "Reggio Emilia": 830013550, "Reggio Calabria": 830011998, "Rimini": 830005998,
}
# Display names, by lefrecce id or Italo code, where neither source is tidy.
NAMES = {
    830009988: "Napoli Afragola", 830006900: "Firenze Campo di Marte", 830005059: "Forlì",
    830002088: "Peschiera del Garda", 830011749: "Lamezia Terme Centrale", 830003213: "Trieste Aeroporto",
    830011789: "Vibo Valentia-Pizzo", 830002666: "San Donà di Piave-Jesolo", 830011505: "Gioia del Colle",
    830007513: "San Benedetto del Tronto", 830011715: "Centola-Palinuro", 830006421: "Firenze S.M.Novella",
    870068600: "Paris Gare de Lyon", 870072319: "Lyon Part-Dieu", 870074100: "Chambéry",
}
# Station name prefix -> city, where the city is not simply the first word of the name.
CITY = {"Reggio Emilia": "Reggio Emilia", "Reggio Calabria": "Reggio Calabria", "Riminifiera": "Rimini",
        "Gioia del Colle": "Gioia del Colle", "Gioia Tauro": "Gioia Tauro", "La Spezia": "La Spezia",
        "San Benedetto": "San Benedetto", "San Donà": "San Donà", "Villa San Giovanni": "Villa San Giovanni"}
# City -> 3-letter code, where the first three letters would collide or read badly.
ABBR = {"Reggio Emilia": "RGE", "Reggio Calabria": "RGC", "Gioia del Colle": "GDC", "Gioia Tauro": "GTA",
        "La Spezia": "SPE", "San Benedetto": "SBT", "San Donà": "SDP", "Villa San Giovanni": "VSG",
        "Barletta": "BLT", "Bolzano": "BZO", "Torano": "TLA", "Bressanone": "BRX",
        "Cassino": "CSN", "Civitanova": "CVM", "Ferrandina": "FRD", "Fortezza": "FTZ", "Modane": "MDN",
        "Monopoli": "MNP", "Parma": "PRM", "Pesaro": "PSR", "Peschiera": "PDG", "Pisciotta": "PPA",
        "Portogruaro": "PGR", "Rovereto": "RVR", "Termoli": "TRM", "Terontola": "TRT", "Treviso": "TRV"}


def cached(name, fetch):
    """Bytes of .cache/<name>, calling fetch() only on a cache miss."""
    p = CACHE / name
    if not p.exists():
        CACHE.mkdir(exist_ok=True)
        p.write_bytes(fetch())
    return p.read_bytes()


def get(url, headers=None, data=None, opener=None):
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    with (opener or urllib.request.build_opener()).open(req, timeout=60) as r:
        return r.read()


def italo_stations():
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    js = {"Content-Type": "application/json", "Accept": "application/json"}
    get("https://biglietti.italotreno.com/api/login", {**js, "X-Anonymous-User": "true"},
        b'{"isAnonymous":true}', op)
    token = next(c.value for c in jar if c.name == "BIGSessionToken")
    h = {**js, "Authorization": f"Bearer {token}", "X-BIG-working-session-id": str(uuid.uuid4())}
    get("https://api-biglietti.italotreno.com/api/v1/working-sessions", h, b"{}", op)
    return get("https://api-biglietti.italotreno.com/api/v1/stations", h, opener=op)


def fold(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def norm(s):
    """Comparable form of a station name: 'FIRENZE S.MARIA' and 'Firenze S. Maria' agree."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", fold(s).replace("c.le", "centrale")).split())


def km(a, b):
    (la1, lo1), (la2, lo2) = [(math.radians(float(x)), math.radians(float(y))) for x, y in (a, b)]
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def frecce_stops():
    """{lefrecce id: (GTFS name, (lat, lon))} for every stop of an FR/FA/FB trip."""
    z = zipfile.ZipFile(io.BytesIO(cached("gtfs.zip", lambda: get(GTFS))))
    rows = lambda n: csv.DictReader(io.TextIOWrapper(z.open(n), "utf-8-sig"))
    routes = {r["route_id"] for r in rows("routes.txt") if r["route_short_name"] in ("FR", "FA", "FB")}
    trips = {t["trip_id"] for t in rows("trips.txt") if t["route_id"] in routes}
    used = {s["stop_id"] for s in rows("stop_times.txt") if s["trip_id"] in trips}
    stops = {s["stop_id"]: s for s in rows("stops.txt")}
    out = {}
    for sid in used:
        s = stops[stops[sid]["parent_station"] or sid]
        out[int(s["stop_id"].rsplit(":", 1)[1])] = (s["stop_name"], (s["stop_lat"], s["stop_lon"]))
    return out


def cruscotto_lookup():
    """GTFS name -> (lefrecce spelling, Frecce flag) or None, matched on cruscotto text and value."""
    raw = cached("cruscotto.json", lambda: get(CRUSCOTTO))
    try:
        data = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:  # served as cp1252 on 2026-10-06 ("Chieti Universit\xe0")
        data = json.loads(raw.decode("cp1252"))
    names = {}
    for e in data:
        flag = bool(e["isF"] or e["FA"] or e["FB"])
        for s in (e["text"], e["value"]):
            if not names.get(norm(s), ("", False))[1]:
                names[norm(s)] = (s, flag)

    def lookup(gtfs_name):
        g = norm(gtfs_name)
        if g in names:
            return names[g]
        for want_flag in (True, False):  # 'PESCARA' -> 'Pescara Centrale', the one flagged entry
            hits = [v for n, v in names.items() if n.startswith(g + " ") and v[1] == want_flag]
            if len(hits) == 1:
                return hits[0]
        return None
    return lookup


def city(name):
    return next((c for p, c in sorted(CITY.items(), key=lambda x: -len(x[0])) if name.startswith(p)),
                name.split()[0].split("-")[0])


def abbr(c):
    return ABBR.get(c) or fold(c)[:3].upper()


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", fold(s)).strip("-")


def build():
    lookup, stops, log = cruscotto_lookup(), frecce_stops(), []
    stations = {}  # lefrecce id -> entry
    for tid, (gname, pos) in stops.items():
        hit = lookup(gname)
        if tid // 10**6 != 830 and not (hit and hit[1]):
            log.append(f"skipped, abroad and not a Frecce destination on trenitalia.com: {gname}")
            continue
        if not hit and tid not in NAMES:
            log.append(f"no trenitalia.com name: {gname} {tid}")
        if hit and not hit[1]:
            log.append(f"kept, not flagged Frecce on trenitalia.com: {hit[0]}")
        stations[tid] = {"n": hit[0] if hit else gname.title(), "t": tid, "i": None, "pos": pos}

    italo = json.loads(cached("italo.json", italo_stations).decode("utf-8"))["stations"]
    italo = [s for s in italo if s["isItaloStation"] and s["stationClass"] != "B"]
    groups = {c: {"n": c, "t": t, "i": None, "g": 1} for c, t in GROUPS.items()}
    frecce = list(stations.values())
    for s in italo:
        if s["isMAC"]:
            c = s["name"].split(" (")[0]
            groups.setdefault(c, {"n": c, "t": None, "i": None, "g": 1})["i"] = s["stationCode"]
            continue
        pos = (s["latitude"], s["longitude"])
        d, near = min(((km(pos, e["pos"]), e) for e in frecce), key=lambda x: x[0])
        if d <= MAX_KM:
            assert near["i"] is None, f"two Italo stations near {near['n']}"
            near.update(i=s["stationCode"], n=s["name"])
        else:
            stations[s["stationCode"]] = {"n": s["name"], "t": None, "i": s["stationCode"]}
            log.append(f"Italo only: {s['name']} ({d:.1f} km from {near['n']})")

    entries = []
    for e in list(groups.values()) + list(stations.values()):
        n = NAMES.get(e["t"]) or NAMES.get(e["i"]) or e["n"]
        c = n if e.get("g") else city(n)
        entries.append({"k": slug(n + (" tutte" if e.get("g") else "")),
                        "n": n + (" (tutte le stazioni)" if e.get("g") else ""),
                        "t": e["t"], "i": e["i"], **({"g": 1} if e.get("g") else {}), "a": abbr(c), "c": c})
    entries.sort(key=lambda e: (not e.get("g"), fold(e["n"])))
    for grp in (e for e in entries if e.get("g") and not e["i"]):
        own = [e["i"] for e in entries if not e.get("g") and e["c"] == grp["c"] and e["i"]]
        if len(own) == 1:  # no Italo MAC, but Italo has one station there: the group is that station
            grp["i"] = own[0]

    code = {e.pop("c"): e["a"] for e in entries}  # city -> abbreviation
    clashes = {a: [c for c in code if code[c] == a] for a in code.values() if list(code.values()).count(a) > 1}
    assert not clashes, f"abbreviation clashes, extend ABBR: {clashes}"
    assert all(re.fullmatch("[A-Z]{3}", a) for a in code.values()), "abbreviations are 3 letters"
    assert len({e["k"] for e in entries}) == len(entries), "duplicate keys"
    return entries, log


if __name__ == "__main__":
    entries, log = build()
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(entries, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(*log, sep="\n")
    g = sum(1 for e in entries if e.get("g"))
    print(f"{len(entries) - g} stations, {g} groups, {sum(1 for e in entries if not e['t'])} Italo-only, "
          f"{OUT.stat().st_size} bytes -> {OUT.relative_to(ROOT)}")
