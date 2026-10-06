"""All user-facing strings, English and Italian (wording approved in the message prototype, rounds 1-7).
Dates and prices are formatted here without the OS locale, so output is identical on every machine."""

EN, IT = "en", "it"

_WD = {EN: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], IT: ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]}
_MO = {EN: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
       IT: ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"]}

S = {
    EN: dict(
        book_i="🎫 Book on Italo", book_t="🎫 Book on Trenitalia", hist="📈 History", now="🔄 Check now", dele="🗑 Delete",
        lowest="lowest", trains="trains", next_="Next cheapest", above="Lowest is above your max", last_check="last check",
        adult="Adult", young="Young", senior="Senior", cheapest="Cheapest fare", left="left", none_found="No matching trains right now.",
        other_fare="cheapest other fare", out="Outbound", ret="Return", total="Total",
        up="Price up", down="New low", under="Under your max", back="Back on sale", gone="no longer available",
        gone_window="purchase window closed (11 days before departure)", gone_sold="sold out on every train",
        last="Last day to buy", tonight="purchase window closes tonight at 23:59",
        still="Still on sale past the deadline",
        still_note="The purchase window should be closed, but a live check just found it on sale — buy now if you still want it.",
        buy_soon="above your max {m} — buy soon, it may rise again", above_max="above your max {m}", under_max="under your max {m}",
        window="Purchase window closes {d} (11-day limit)", rt="Round trip cheaper", rt_up="Round trip pricier",
        out_ch="Outbound changed", ret_ch="Return changed", unchanged="unchanged",
        list_t="Your watches", list_empty="No watches yet. Tap 🚄 TrainWatcher to create one.",
        ack="⏳ Got it — checking prices, results in about a minute.", searching="⏳ Searching… results in about a minute.",
        search="Search", live="live search, not saved", watch_this="➕ Watch this", created="✅ Watch created: {w}. You'll get alerts when prices move.",
        del_q="🗑 Delete watch {n}? <b>{w}</b>\nIts price history stays viewable in /past.", del_yes="✅ Delete", del_no="↩️ Keep",
        deleted="🗑 Watch {n} deleted ({w}).", kept="↩️ Watch {n} kept.", past_t="Past watches", past_empty="No past watches yet.",
        welcome="Welcome to TrainWatcher 👋\nSearch trains once, or watch a route and get alerts when prices move.\nTap 🚄 TrainWatcher next to the message box to start.",
        lang_q="Choose your language:", lang_set="Language set to English 🇬🇧.",
        help="/list — your watches\n/past — finished watches\n/language — 🇮🇹/🇬🇧\nTap 🚄 TrainWatcher to search or create a watch.",
        load_warn="⚠️ You have many watches: checks will be less frequent than usual.",
        stale="⚠️ prices from {t}", invalid="⚠️ That form could not be used: {why}.",
    ),
    IT: dict(
        book_i="🎫 Prenota su Italo", book_t="🎫 Prenota su Trenitalia", hist="📈 Storico", now="🔄 Aggiorna", dele="🗑 Elimina",
        lowest="il più basso", trains="treni", next_="Altri prezzi", above="Il prezzo più basso supera il tuo massimo di", last_check="ultimo controllo",
        adult="Adulto", young="Giovane", senior="Senior", cheapest="Più economica", left="posti", none_found="Nessun treno corrispondente al momento.",
        other_fare="tariffa più economica alternativa", out="Andata", ret="Ritorno", total="Totale",
        up="Prezzo in aumento", down="Nuovo minimo", under="Sotto il tuo massimo", back="Di nuovo in vendita", gone="non più disponibile",
        gone_window="vendita chiusa (11 giorni prima della partenza)", gone_sold="esaurita su tutti i treni",
        last="Ultimo giorno per acquistare", tonight="la vendita chiude stasera alle 23:59",
        still="Ancora in vendita oltre la scadenza",
        still_note="La vendita dovrebbe essere terminata, ma da un controllo online risulta ancora disponibile — acquista ora se ti interessa ancora.",
        buy_soon="sopra il tuo massimo di {m} — compra presto, potrebbe salire ancora", above_max="sopra il tuo massimo di {m}",
        under_max="sotto il tuo massimo di {m}", window="Vendita aperta fino a {d} (limite di 11 giorni)",
        rt="Andata e ritorno più economici", rt_up="Andata e ritorno più cari", out_ch="Andata cambiata", ret_ch="Ritorno cambiato", unchanged="invariato",
        list_t="I tuoi monitoraggi", list_empty="Nessun monitoraggio. Tocca 🚄 TrainWatcher per crearne uno.",
        ack="⏳ Ricevuto — controllo i prezzi, risultato tra circa un minuto.", searching="⏳ Cerco… risultati tra circa un minuto.",
        search="Ricerca", live="ricerca in tempo reale, non salvata", watch_this="➕ Monitora",
        created="✅ Monitoraggio creato: {w}. Riceverai avvisi quando i prezzi cambiano.",
        del_q="🗑 Eliminare il monitoraggio {n}? <b>{w}</b>\nLo storico dei prezzi resta visibile in /past.", del_yes="✅ Elimina", del_no="↩️ Tieni",
        deleted="🗑 Monitoraggio {n} eliminato ({w}).", kept="↩️ Monitoraggio {n} mantenuto.", past_t="Monitoraggi conclusi", past_empty="Nessun monitoraggio concluso.",
        welcome="Benvenuto in TrainWatcher 👋\nCerca treni al volo, oppure monitora una tratta e ricevi avvisi quando i prezzi cambiano.\nTocca 🚄 TrainWatcher accanto alla casella dei messaggi per iniziare.",
        lang_q="Scegli la lingua:", lang_set="Lingua impostata: italiano 🇮🇹.",
        help="/list — i tuoi monitoraggi\n/past — monitoraggi conclusi\n/language — 🇮🇹/🇬🇧\nTocca 🚄 TrainWatcher per cercare o creare un monitoraggio.",
        load_warn="⚠️ Hai molti monitoraggi: i controlli saranno meno frequenti del solito.",
        stale="⚠️ prezzi delle {t}", invalid="⚠️ Il modulo non è utilizzabile: {why}.",
    ),
}


def t(lang, key, **kw):
    s = S.get(lang, S[EN])[key]
    return s.format(**kw) if kw else s


def price(x, lang):
    """€29.90 / 29,90 €; whole euros keep the cents (operators quote cents)."""
    return f"€{x:.2f}" if lang == EN else f"{x:.2f} €".replace(".", ",")


def day(d, lang, weekday=True, year=None):
    s = f"{_WD[lang][d.weekday()]} {d.day} {_MO[lang][d.month - 1]}" if weekday else f"{d.day} {_MO[lang][d.month - 1]}"
    return s + (f" '{d.year % 100:02d}" if year else "")


def month(y, m, lang):
    return f"{_MO[lang][m - 1]} {y}"
