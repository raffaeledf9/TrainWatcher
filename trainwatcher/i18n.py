"""All user-facing strings, English and Italian (wording approved in the message prototype, rounds 1-7).
Dates and prices are formatted here without the OS locale, so output is identical on every machine."""

EN, IT = "en", "it"

_WD = {EN: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], IT: ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]}
_MO = {EN: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
       IT: ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"]}

S = {
    EN: dict(
        book_i="🎫 Book on Italo", book_t="🎫 Book on Trenitalia", hist="📈 History", now="🔄 Check now", dele="🗑 Delete",
        lowest="lowest", trains="trains", train="train", on_sale="Now on sale", more_ties="…and {n} more trains at this price (all of them in 📋)", next_="Next cheapest", above="Lowest is above your max", last_check="last check",
        adult="Adult", young="Young", senior="Senior", cheapest="Cheapest fare", left="left", none_found="No matching trains right now.",
        other_fare="cheapest other fare", out="Outbound", ret="Return", total="Total",
        up="Price up", down="New low", under="Under your max", back="Back on sale", gone="no longer available",
        gone_window="purchase window closed ({n} days before departure)", gone_sold="sold out on every train",
        last="Last day to buy", tonight="purchase window closes tonight at 23:59",
        still="Still on sale past the deadline",
        still_note="The purchase window should be closed, but a live check just found it on sale — buy now if you still want it.",
        buy_soon="above your max {m} — buy soon, it may rise again", above_max="above your max {m}", under_max="under your max {m}",
        window="Purchase window closes {d} ({n}-day limit)", rt="Round trip cheaper", rt_up="Round trip pricier",
        out_ch="Outbound changed", ret_ch="Return changed", unchanged="unchanged",
        list_t="Your watches", list_empty="No watches yet. Tap 🚄 TrainWatcher to create one.",
        ack="⏳ Got it — checking prices, results in about a minute.", searching="⏳ Searching… results in about a minute.",
        search="Search", live="live search, not saved", watch_this="➕ Watch this", created="✅ Watch created: {w}. You'll get alerts when prices move.",
        del_q="🗑 Delete watch {n}? <b>{w}</b>\nIts price history stays viewable in /past.", del_yes="✅ Delete", del_no="↩️ Keep",
        deleted="🗑 Watch {n} deleted ({w}).", kept="↩️ Watch {n} kept.", past_t="Past watches", past_empty="No past watches yet.", more_past="…and {n} earlier ones",
        welcome="Welcome to TrainWatcher 👋\nSearch trains once, or watch a route and get alerts when prices move.\nTap 🚄 TrainWatcher next to the message box to start.",
        lang_q="Choose your language:", lang_set="Language set to English 🇬🇧.",
        help="/list — your watches\n/past — finished watches\n/language — 🇮🇹/🇬🇧\nTap 🚄 TrainWatcher to search or create a watch.",
        load_warn="⚠️ The bot is busy: checks will be less frequent than usual.",
        stale="⚠️ prices from {t}", invalid="⚠️ That form could not be used: {why}.",
        req_sent="📨 Request sent. You'll get a message here once the bot's owner approves it.",
        req_wait="⏳ Your request is still waiting for the owner's approval.",
        req_new="👤 <b>Access request</b>\n{who}\nwants to use TrainWatcher.",
        btn_allow="✅ Allow", btn_deny="❌ Deny", btn_remove="🚫 Remove {name}", btn_readd="✅ Allow {name} again",
        allowed_owner="✅ {name} can now use the bot. Remove access anytime with /friends.",
        denied_owner="❌ Request from {name} declined.", removed_owner="🚫 {name} can no longer use the bot; their watches are stopped.",
        allowed_friend="✅ You're in! Search trains once, or watch a route and get alerts when prices move.\nNote: the bot's owner runs it and can see your watches.",
        removed_friend="Your access to TrainWatcher has ended.",
        invite_text="🔗 Invite link (one for everyone):\n{link}\n\nWhoever opens it can ask for access; you approve each person once. /invite reset makes a new link and the old one stops working.",
        invite_reset="🔄 New invite link; the old one no longer works:\n{link}",
        friends_t="👥 <b>Friends</b>", friends_empty="No friends yet: share the link from /invite.",
        st_pending="waiting", st_allowed="allowed", st_denied="declined", st_removed="removed",
        help_owner="/invite — invite link\n/friends — who can use the bot",
        rate_limited="⏳ Too many requests in the last hour: try again a bit later.",
        cap_reached="You can have at most {n} active watches: delete one from /list first.",
        load_owner="⚠️ Price checks are at {p}% of capacity ({n} active watches): they'll get less frequent. /friends to manage who uses the bot.",
        fail="⚠️ Price checks for {ops} have failed 3 times in a row. Watches show the last prices that worked; I'll tell you when it's fixed.",
        no_answer="⚠️ {ops} didn't answer this time: the list may be incomplete.", no_chart="📈 No price history yet.",
        recovered="✅ Price checks are working again.", op_ok="✅ Price checks for {ops} are working again.",
        job_dead="⚠️ Price checks have stopped finishing (the GitHub job is failing or not starting). I'll tell you when they're back.",
        worker_dead="⚠️ The Cloudflare Worker isn't starting price checks (Worker down or GitHub token expired). Checks go on only every few hours; buttons and commands may not answer.",
        dispatch_fail="⚠️ GitHub refused to start a price check: the GitHub token has probably expired. Checks go on only every few hours; ask Claude to install a new token.",
        weekly="🩺 Weekly check — active watches: {w} · checks: {c} · failed runs this week: {f}",
        token="🔑 The GitHub token expires on {d}. Make a new one ({url}) and ask Claude to install it, otherwise checks slow down to every few hours.",
    ),
    IT: dict(
        book_i="🎫 Prenota su Italo", book_t="🎫 Prenota su Trenitalia", hist="📈 Storico", now="🔄 Aggiorna", dele="🗑 Elimina",
        lowest="il più basso", trains="treni", train="treno", on_sale="In vendita", more_ties="…e altri {n} treni a questo prezzo (tutti in 📋)", next_="Altri prezzi", above="Il prezzo più basso supera il tuo massimo di", last_check="ultimo controllo",
        adult="Adulto", young="Giovane", senior="Senior", cheapest="Più economica", left="posti", none_found="Nessun treno corrispondente al momento.",
        other_fare="tariffa più economica alternativa", out="Andata", ret="Ritorno", total="Totale",
        up="Prezzo in aumento", down="Nuovo minimo", under="Sotto il tuo massimo", back="Di nuovo in vendita", gone="non più disponibile",
        gone_window="vendita chiusa ({n} giorni prima della partenza)", gone_sold="esaurita su tutti i treni",
        last="Ultimo giorno per acquistare", tonight="la vendita chiude stasera alle 23:59",
        still="Ancora in vendita oltre la scadenza",
        still_note="La vendita dovrebbe essere terminata, ma da un controllo online risulta ancora disponibile — acquista ora se ti interessa ancora.",
        buy_soon="sopra il tuo massimo di {m} — compra presto, potrebbe salire ancora", above_max="sopra il tuo massimo di {m}",
        under_max="sotto il tuo massimo di {m}", window="Vendita aperta fino a {d} (limite di {n} giorni)",
        rt="Andata e ritorno più economici", rt_up="Andata e ritorno più cari", out_ch="Andata cambiata", ret_ch="Ritorno cambiato", unchanged="invariato",
        list_t="I tuoi monitoraggi", list_empty="Nessun monitoraggio. Tocca 🚄 TrainWatcher per crearne uno.",
        ack="⏳ Ricevuto — controllo i prezzi, risultato tra circa un minuto.", searching="⏳ Cerco… risultati tra circa un minuto.",
        search="Ricerca", live="ricerca in tempo reale, non salvata", watch_this="➕ Monitora",
        created="✅ Monitoraggio creato: {w}. Riceverai avvisi quando i prezzi cambiano.",
        del_q="🗑 Eliminare il monitoraggio {n}? <b>{w}</b>\nLo storico dei prezzi resta visibile in /past.", del_yes="✅ Elimina", del_no="↩️ Tieni",
        deleted="🗑 Monitoraggio {n} eliminato ({w}).", kept="↩️ Monitoraggio {n} mantenuto.", past_t="Monitoraggi conclusi", past_empty="Nessun monitoraggio concluso.", more_past="…e altri {n} precedenti",
        welcome="Benvenuto in TrainWatcher 👋\nCerca treni al volo, oppure monitora una tratta e ricevi avvisi quando i prezzi cambiano.\nTocca 🚄 TrainWatcher accanto alla casella dei messaggi per iniziare.",
        lang_q="Scegli la lingua:", lang_set="Lingua impostata: italiano 🇮🇹.",
        help="/list — i tuoi monitoraggi\n/past — monitoraggi conclusi\n/language — 🇮🇹/🇬🇧\nTocca 🚄 TrainWatcher per cercare o creare un monitoraggio.",
        load_warn="⚠️ Il bot è molto carico: i controlli saranno meno frequenti del solito.",
        stale="⚠️ prezzi delle {t}", invalid="⚠️ Il modulo non è utilizzabile: {why}.",
        req_sent="📨 Richiesta inviata. Riceverai un messaggio qui quando il proprietario del bot l'avrà approvata.",
        req_wait="⏳ La tua richiesta è ancora in attesa di approvazione.",
        req_new="👤 <b>Richiesta di accesso</b>\n{who}\nvuole usare TrainWatcher.",
        btn_allow="✅ Consenti", btn_deny="❌ Rifiuta", btn_remove="🚫 Rimuovi {name}", btn_readd="✅ Riammetti {name}",
        allowed_owner="✅ {name} ora può usare il bot. Puoi togliere l'accesso quando vuoi con /friends.",
        denied_owner="❌ Richiesta di {name} rifiutata.", removed_owner="🚫 {name} non può più usare il bot; i suoi monitoraggi sono fermati.",
        allowed_friend="✅ Sei dentro! Cerca treni al volo, oppure monitora una tratta e ricevi avvisi quando i prezzi cambiano.\nNota: il bot è gestito dal suo proprietario, che può vedere i tuoi monitoraggi.",
        removed_friend="Il tuo accesso a TrainWatcher è terminato.",
        invite_text="🔗 Link di invito (uno solo per tutti):\n{link}\n\nChi lo apre può chiedere l'accesso; approvi ogni persona una volta. /invite reset crea un nuovo link e il vecchio smette di funzionare.",
        invite_reset="🔄 Nuovo link di invito; il vecchio non funziona più:\n{link}",
        friends_t="👥 <b>Amici</b>", friends_empty="Nessun amico ancora: condividi il link di /invite.",
        st_pending="in attesa", st_allowed="ammesso", st_denied="rifiutato", st_removed="rimosso",
        help_owner="/invite — link di invito\n/friends — chi può usare il bot",
        rate_limited="⏳ Troppe richieste nell'ultima ora: riprova tra un po'.",
        cap_reached="Puoi avere al massimo {n} monitoraggi attivi: prima eliminane uno da /list.",
        load_owner="⚠️ I controlli dei prezzi sono al {p}% della capacità ({n} monitoraggi attivi): diventeranno meno frequenti. /friends per gestire chi usa il bot.",
        fail="⚠️ I controlli dei prezzi per {ops} sono falliti 3 volte di fila. I monitoraggi mostrano gli ultimi prezzi validi; ti avviso quando è risolto.",
        no_answer="⚠️ Nessuna risposta da {ops} questa volta: l'elenco potrebbe essere incompleto.", no_chart="📈 Ancora nessuno storico dei prezzi.",
        recovered="✅ I controlli dei prezzi funzionano di nuovo.", op_ok="✅ I controlli dei prezzi per {ops} funzionano di nuovo.",
        job_dead="⚠️ I controlli dei prezzi non arrivano più alla fine (il job GitHub fallisce o non parte). Ti avviso quando riprendono.",
        worker_dead="⚠️ Il Worker Cloudflare non avvia i controlli dei prezzi (Worker fermo o token GitHub scaduto). I controlli continuano solo ogni qualche ora; pulsanti e comandi potrebbero non rispondere.",
        dispatch_fail="⚠️ GitHub ha rifiutato di avviare un controllo: probabilmente il token GitHub è scaduto. I controlli continuano solo ogni qualche ora; fai installare a Claude un nuovo token.",
        weekly="🩺 Controllo settimanale — monitoraggi attivi: {w} · controlli: {c} · esecuzioni fallite questa settimana: {f}",
        token="🔑 Il token GitHub scade il {d}. Creane uno nuovo ({url}) e fallo installare a Claude, altrimenti i controlli rallentano a uno ogni qualche ora.",
    ),
}


# model.Invalid reasons (English in the code) for the form-refusal message
_REASONS_IT = {
    "bad form": "modulo non valido", "bad dates": "date non valide", "bad max price": "prezzo massimo non valido", "bad operators": "operatori non validi",
    "bad passenger type": "tipo di passeggero non valido", "bad time window": "fascia oraria non valida",
    "dates are in the past": "le date sono passate", "dates too far or too many": "date troppo lontane o troppe",
    "fare not available for this passenger type": "tariffa non disponibile per questo passeggero",
    "fare not available for this trip": "tariffa non disponibile per questo viaggio",
    "origin and destination are the same": "partenza e arrivo coincidono", "return before outbound": "il ritorno precede l'andata",
    "time window ends before it starts": "la fascia oraria finisce prima di iniziare", "unknown class": "classe sconosciuta",
    "unknown fare": "tariffa sconosciuta", "unknown station": "stazione sconosciuta", "unsupported form version": "versione del modulo non supportata",
}


def reason(lang, why):
    return _REASONS_IT.get(why, why) if lang == IT else why


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
