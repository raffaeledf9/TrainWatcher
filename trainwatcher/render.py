"""Messages and buttons exactly as approved in the message prototype (branch prototype/bot-messages):
status view = style A, alerts = style C layout + A details, /list = one info-rich button per watch.
Every function returns (html_text, inline_keyboard_rows)."""
from datetime import date
from html import escape

from trainwatcher import stations
from trainwatcher.i18n import EN, day, month, price, t
from trainwatcher.offers import ITALO

OPS = {"FR": "Frecciarossa", "FA": "Frecciargento", "FB": "Frecciabianca", "IT": "Italo"}
MAX_TIES = 8  # trains listed in full; beyond that the compact list by day (a flat month had 120 ties)
TRENITALIA_URL = "https://www.lefrecce.it/Channels.Website.WEB/#/?tab=biglietto"


# ---------- small pieces ----------
def station_name(key, lang):
    n = stations.by_key(key)["n"]
    return n.replace("(tutte le stazioni)", "(tutte)" if lang != EN else "(all)")


def abbr(key):
    return stations.abbreviation(stations.by_key(key))


def route(watch, lang, short=False):
    a, b = watch.legs[0].origin, watch.legs[0].destination
    arrow = " ⇄ " if watch.round_trip else " → "
    return (abbr(a) + arrow + abbr(b)) if short else escape(station_name(a, lang) + arrow + station_name(b, lang))


def days_label(leg, lang, today=None):
    today = today or date.today()
    ds = leg.dates()
    yr = lambda d: d.year != today.year
    if len(leg.days) == 1 and len(leg.days[0]) == 7:
        return month(ds[0].year, ds[0].month, lang)
    if len(ds) == 1:
        return day(ds[0], lang, year=yr(ds[0]))
    a, b = ds[0], ds[-1]
    if (a.year, a.month) == (b.year, b.month):
        return f"{a.day}–{day(b, lang, weekday=False, year=yr(b))}"
    return f"{day(a, lang, weekday=False)} – {day(b, lang, weekday=False, year=yr(b))}"


def watch_days(watch, lang, today=None):
    out = days_label(watch.legs[0], lang, today)
    return out + (" → " + days_label(watch.legs[1], lang, today) if watch.round_trip and watch.legs[1].days != watch.legs[0].days else "")


def fare_label(watch, lang):
    return ", ".join(f.split(":", 1)[1] for f in watch.fares) if watch.fares else t(lang, "cheapest")


def short_train(o):
    return ("IT " if o.operator == ITALO else o.category + " ") + o.train


def stop(raw, key):
    """The station a train uses where the leg has a city group, without the city ('Milano Rogoredo' → 'Rogoredo');
    None where the leg has a single station."""
    e = stations.by_key(key)
    if not e.get("g"):
        return None
    name, city = stations.display_name(raw), e["n"].split(" (")[0]
    return (name[len(city):].strip() if name.lower().startswith(city.lower() + " ") else "") or name


def times(o, leg=None, arrow="→", html=True):
    """'06:15→09:24', or '06:25 Rogoredo → 09:24' when the leg starts or ends at a city group."""
    a, b = (stop(o.origin, leg.origin), stop(o.destination, leg.destination)) if leg else (None, None)
    if not (a or b):
        return f"{o.dep[11:16]}{arrow}{o.arr[11:16]}"
    f = escape if html else str
    return f"{o.dep[11:16]}{' ' + f(a) if a else ''} → {o.arr[11:16]}{' ' + f(b) if b else ''}"


def departs(o, leg):
    """Departure time for one-line lists, with the stations used at city-group ends ('06:25 Rogoredo', '06:15 → Tiburtina')."""
    a, b = stop(o.origin, leg.origin), stop(o.destination, leg.destination)
    return o.dep[11:16] + (f" {escape(a)}" if a else "") + (f" → {escape(b)}" if b else "")


def train_line(o, lang, with_day, leg=None):
    d = (day(date.fromisoformat(o.dep[:10]), lang) + " ") if with_day else ""
    seats = f" · {o.seats} {t(lang, 'left')}" if o.seats else ""
    return f"• {d}{times(o, leg)} <b>{OPS.get(o.category, o.category)} {o.train}</b> · {o.cls} · {escape(o.fare)}{seats}"


def italo_url(leg, d, passenger):
    a, b = stations.by_key(leg.origin).get("i"), stations.by_key(leg.destination).get("i")
    if not (a and b):
        return None
    n = {"adult": "adt=1&yng=0&snr=0", "young": "adt=0&yng=1&snr=0", "senior": "adt=0&yng=0&snr=1"}[passenger]
    return (f"https://biglietti.italotreno.com/it/booking/ricerca-treni?osc={a}&dsc={b}&jt=single&od={d:%d}%2F{d:%m}%2F{d:%Y}"
            f"&id=&{n}&chd=0&inf=0&pet=0&promo=&lang=it&startSearch=true")


def book_rows(watch, lang, d=None, today=None):
    leg = watch.legs[0]
    row = []
    d = d or max(leg.dates()[0], today or date.today())  # a past day would open an empty Italo search
    ops = [op for op in watch.operators if not watch.fares or any(op in f.split(":")[0] for f in watch.fares)]
    if "I" in ops and (u := italo_url(leg, d, watch.passenger)):
        row.append({"text": t(lang, "book_i"), "url": u})
    if "T" in ops:
        row.append({"text": t(lang, "book_t"), "url": TRENITALIA_URL})
    return [row] if row else []


def book_train(o, watch, leg):
    u = italo_url(leg, date.fromisoformat(o.dep[:10]), watch.passenger) if o.operator == ITALO else TRENITALIA_URL
    return {"text": f"🎫 {o.dep[11:16]} {short_train(o)}", "url": u or TRENITALIA_URL}


def actions(watch, lang):
    return [[{"text": t(lang, "hist"), "callback_data": f"hi:{watch.id}"}, {"text": t(lang, "now"), "callback_data": f"nw:{watch.id}"},
             {"text": t(lang, "dele"), "callback_data": f"de:{watch.id}"}]]


def last_check(at, lang):
    return f"🔄 {t(lang, 'last_check')} {at:%H:%M}"


# ---------- status view (style A) ----------
def compact_ties(ties, leg, lang, budget=None):
    """Many trains at one price: every one of them by day ("mar 10 nov: 07:00 Centrale, 18:10 Rogoredo"), train
    number and class dropped (Owner decision 2026-10-07). budget: max characters (alerts), the rest summarised."""
    mixed = len({o.operator for o in ties}) > 1
    by_day = {}
    for o in sorted(ties, key=lambda o: o.dep):
        by_day.setdefault(o.dep[:10], []).append((f"{o.category} " if mixed else "") + departs(o, leg))
    lines, shown = [], 0
    for d, items in by_day.items():
        line = f"• {day(date.fromisoformat(d), lang)}: " + ", ".join(items)
        if budget is not None and sum(len(x) + 1 for x in lines) + len(line) > budget:
            lines.append(t(lang, "more_ties", n=len(ties) - shown))
            break
        lines.append(line)
        shown += len(items)
    return lines


def _leg_block(view, leg, lang):
    lines = []
    multi = len(leg.dates()) > 1
    if view["low"] is None:
        lines.append(t(lang, "none_found"))
        if view.get("other_fare"):
            o = view["other_fare"]
            lines.append(f"ℹ️ {t(lang, 'other_fare')}: {price(o.price, lang)} {escape(o.fare)}")
        return lines
    n = len(view["ties"])
    lines.append(f"<b>{price(view['low'], lang)}</b> {t(lang, 'lowest')} · {n} {t(lang, 'trains' if n > 1 else 'train')}")
    if n > MAX_TIES:
        lines += compact_ties(view["ties"], leg, lang)
    else:
        lines += [train_line(o, lang, multi, leg) for o in view["ties"]]
    if view["others"]:
        lines += ["", f"<i>{t(lang, 'next_')}</i>"]
        lines += [f"• {price(o.price, lang)} {(day(date.fromisoformat(o.dep[:10]), lang) + ' ') if multi else ''}{departs(o, leg)} {short_train(o)} · {escape(o.fare)}"
                  for o in view["others"]]
    return lines


def status(watch, views, checked_at, lang, today=None, search=False, total=None, stale_since=None, search_id=None, failed=None,
           part=None):
    """views: one dict per leg {low, ties, others, other_fare}, None for a leg without days in this part;
    total: round-trip total or None. part: (i, n, "YYYY-MM") when a period spanning months is sent one month per
    message; the footer and the watch buttons go on the last one."""
    pax = t(lang, watch.passenger)
    when = watch_days(watch, lang, today) + (f" · <b>{month(int(part[2][:4]), int(part[2][5:]), lang)}</b> ({part[0]}/{part[1]})" if part else "")
    if search:
        head = [f"🔎 <b>{t(lang, 'search')}</b> · {route(watch, lang, short=True)} · {when} · 👤 {pax} · 💶 {escape(fare_label(watch, lang))}"]
    else:
        head = [f"🚄 <b>{route(watch, lang)}</b>", f"📅 {when} · 👤 {pax} · 💶 {escape(fare_label(watch, lang))}"]
    body = []
    for i, (view, leg) in enumerate(zip(views, watch.legs)):
        if view is None:
            continue
        if watch.round_trip:
            body += ["", f"{'→' if i == 0 else '←'} <b>{t(lang, 'out' if i == 0 else 'ret')}</b> · {days_label(leg, lang, today)}"]
        else:
            body.append("")
        body += _leg_block(view, leg, lang)
    if total is not None:
        body += ["", f"{t(lang, 'total')} <b>{price(total, lang)}</b>"]
    shown = [v for v in views if v is not None]
    cheapest = (shown[0].get("ties") if shown else None) or [None]
    kb = book_rows(watch, lang, cheapest[0] and date.fromisoformat(cheapest[0].dep[:10]), today)
    if part and part[0] < part[1]:
        return "\n".join(head + body), kb
    low = total if watch.round_trip else (views[0] or {}).get("low")
    tail = [""]
    if not search and watch.max_price is not None and low is not None and low > watch.max_price:
        tail.append(f"⚠️ {t(lang, 'above')} <b>{price(watch.max_price, lang)}</b>")
    if failed:
        tail.append(t(lang, "no_answer", ops=" + ".join(failed)))
    if stale_since:
        tail.append(t(lang, "stale", t=f"{stale_since:%H:%M}"))
    tail.append(f"🔄 {t(lang, 'live')}" if search else last_check(checked_at, lang))
    kb += [[{"text": t(lang, "watch_this"), "callback_data": f"wt:{search_id}"}]] if search else actions(watch, lang)
    return "\n".join(head + body + tail), kb


# ---------- alerts (C layout + A details) ----------
TITLES = {"drop": ("📉", "down"), "rise": ("📈", "up"), "under_max": ("✅", "under"), "back": ("🔁", "back"), "on_sale": ("🆕", "on_sale"),
          "last_day": ("⏰", "last"), "still_on_sale": ("🍀", "still")}


def alert(watch, events, views, checked_at, lang, today=None):
    """One message for all events of one Watch in one Run (baseline events are sent as the status view)."""
    sections, trains = [], []
    ties = [(o, leg) for v, leg in zip(views, watch.legs) for o in v.get("ties", [])]
    for e in events:
        if e["kind"] == "gone":
            sections.append([f"🚫 <b>{escape(fare_label(watch, lang))} {t(lang, 'gone')}</b>", f"{route(watch, lang)} · {watch_days(watch, lang, today)}", "",
                             t(lang, "gone_window" if e["reason"] == "window" else "gone_sold")])
            continue
        if watch.round_trip and e["kind"] in ("drop", "rise"):
            sections.append(_round_trip_alert(watch, e, views, lang, today))
            trains += [(v.get("pair") or v["ties"][0], leg) for v, leg in zip(views, watch.legs) if v.get("pair") or v.get("ties")]
            continue
        icon, key = TITLES[e["kind"]]
        lines = [f"{icon} <b>{t(lang, key)}</b> · {escape(fare_label(watch, lang))}", f"{route(watch, lang)} · {watch_days(watch, lang, today)}", ""]
        multi = any(len(l.dates()) > 1 for l in watch.legs)
        old = f" <s>{price(e['prev'], lang)}</s>" if e["kind"] in ("drop", "rise") and e.get("prev") is not None else ""
        if len(ties) > MAX_TIES:
            lines.append(f"<b>{price(ties[0][0].price, lang)}</b>{old} · {len(ties)} {t(lang, 'trains')}")
            lines += compact_ties([o for o, _ in ties], ties[0][1], lang, budget=2500)
        for o, leg in (ties if len(ties) <= MAX_TIES else []):
            d = (day(date.fromisoformat(o.dep[:10]), lang) + " ") if multi else ""
            lines.append(f"<b>{price(o.price, lang)}</b>{old} — {OPS.get(o.category, o.category)} {o.train} {d}{times(o, leg)} · {o.cls}")
        lines.append("")
        lines += _notes(watch, e, lang)
        sections.append(lines)
        trains += ties[:MAX_TIES]
    seen, row = set(), []
    for o, leg in trains:
        if (o.train, o.dep) not in seen:
            seen.add((o.train, o.dep))
            row.append(book_train(o, watch, leg))
    kb = [row[i:i + 3] for i in range(0, min(len(row), 6), 3)] + actions(watch, lang)
    return "\n\n".join("\n".join(s).strip() for s in sections), kb


def _notes(watch, e, lang):
    out = []
    if e["kind"] == "still_on_sale":
        return [f"🛒 {t(lang, 'still_note')}"]
    if e["kind"] == "last_day":
        return [f"🛒 {t(lang, 'tonight')}"]
    if "max" in e:
        m = price(e["max"], lang)
        if e["under_max"]:
            out.append(f"✅ {t(lang, 'under_max', m=m)}")
        else:
            out.append(f"⚠️ {t(lang, 'buy_soon' if e['kind'] == 'rise' else 'above_max', m=m)}")
    from trainwatcher.alerts import window_days
    w = window_days(watch)
    if w is not None:
        from datetime import timedelta
        out.append(f"🛒 {t(lang, 'window', d=day((e.get('low_day') or watch.first_day) - timedelta(days=w), lang))}")
    return out


def _round_trip_alert(watch, e, views, lang, today):
    up = e["kind"] == "rise"
    lines = [f"{'📈' if up else '📉'} <b>{t(lang, 'rt_up' if up else 'rt')}</b> · {escape(fare_label(watch, lang))}",
             f"{route(watch, lang)} · {watch_days(watch, lang, today)}", "",
             f"{t(lang, 'total')} <b>{price(e['low'], lang)}</b>" + (f" <s>{price(e['prev'], lang)}</s>" if e.get("prev") is not None else "")]
    legs, prev = e.get("legs") or {}, e.get("prev_legs") or {}
    for i, k in enumerate(("out", "ret")):
        o = views[i].get("pair") or (views[i]["ties"][0] if views[i].get("ties") else None)
        if o is None:
            continue
        changed = prev.get(k) is not None and legs.get(k) != prev.get(k)
        label = t(lang, ("out_ch" if i == 0 else "ret_ch") if changed else ("out" if i == 0 else "ret"))
        old = f" <s>{price(prev[k], lang)}</s>" if changed else ""
        same = "" if changed else f" ({t(lang, 'unchanged')})"
        lines.append(f"{'→' if i == 0 else '←'} {label}: <b>{price(o.price, lang)}</b>{old} — {OPS.get(o.category, o.category)} {o.train} {times(o, watch.legs[i])}{same}")
    lines.append("")
    if "max" in e:
        m = price(e["max"], lang)
        lines.append(f"✅ {t(lang, 'under_max', m=m)}" if e["under_max"] else f"⚠️ {t(lang, 'above_max', m=m)}")
    return lines


# ---------- /list (L2 v2) and small replies ----------
def watch_list(items, lang, today=None):
    """items: [(watch, lowest_offer_or_None, lowest_price_or_None, checked_at_or_None)] in display order."""
    if not items:
        return t(lang, "list_empty"), []
    nums = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    recap, kb = [], []
    for i, (w, o, low, at) in enumerate(items):
        n = nums[i] if i < len(nums) else f"{i + 1}."
        recap.append(f"{n} {route(w, lang, short=True)} · {watch_days(w, lang, today)}" + (f" · {last_check(at, lang)}" if at else ""))
        fare = f" · {o.fare} {price(low, lang)}" if o is not None else (f" · {price(low, lang)}" if low is not None else "")
        kb.append([{"text": f"{n} {station_name(w.legs[0].origin, lang)}{' ⇄ ' if w.round_trip else ' → '}{station_name(w.legs[0].destination, lang)} · {watch_days(w, lang, today)}{fare}",
                    "callback_data": f"st:{w.id}"}])
    return f"📋 <b>{t(lang, 'list_t')}</b>\n\n" + "\n".join(recap), kb


def delete_question(n, watch, lang):
    return t(lang, "del_q", n=n, w=route(watch, lang, short=True) + " · " + watch_days(watch, lang)), \
        [[{"text": t(lang, "del_yes"), "callback_data": f"dy:{watch.id}"}, {"text": t(lang, "del_no"), "callback_data": f"dn:{watch.id}"}]]
