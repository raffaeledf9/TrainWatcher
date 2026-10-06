// TrainWatcher Worker: Telegram webhook with instant replies from snapshots (ADR 0003), D1 command queue for the
// GitHub job, on-demand and 5-minute dispatch of the price-check workflow. Talks only to Telegram, GitHub and D1:
// the train operators block Cloudflare (ADR 0001).

const MIN = 60_000;
// Fallback strings until the job has published strings:<lang> to D1 (the job's i18n.py is the source of truth).
const FALLBACK = { ack: "⏳ Got it — checking prices, results in about a minute.", list_empty: "No watches yet.", past_empty: "No past watches yet.",
  welcome: "Welcome to TrainWatcher 👋", lang_q: "Choose your language:", help: "/list · /past · /language", deleted: "🗑 Watch {n} deleted.",
  kept: "↩️ Watch {n} kept.", del_yes: "✅ Delete", del_no: "↩️ Keep", lang_set: "OK" };

export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    try {
      if (req.method === "POST" && url.pathname === "/tg") return await telegram(req, env, ctx);
      if (url.pathname.startsWith("/job/")) return await jobApi(req, env, url.pathname);
      return new Response("TrainWatcher", { status: 200 });
    } catch (e) {
      console.log("error", url.pathname, String(e).slice(0, 200));
      // Telegram re-delivers non-2xx updates, so the webhook always answers 200.
      return url.pathname === "/tg" ? new Response("ok") : new Response("error", { status: 500 });
    }
  },

  async scheduled(event, env, ctx) {
    const minute = new Date(event.scheduledTime).getUTCMinutes();
    const pending = await env.DB.prepare("SELECT COUNT(*) AS n FROM queue WHERE taken_at IS NULL").first("n");
    if (minute % 5 === 0) await dispatch(env, "tick");
    else if (pending > 0) await dispatch(env, "queue");
  },
};

// ---------- Telegram webhook ----------
async function telegram(req, env, ctx) {
  if (!safeEqual(req.headers.get("X-Telegram-Bot-Api-Secret-Token") || "", env.TG_SECRET)) return new Response("ok");
  const u = await req.json();
  const msg = u.message, cb = u.callback_query;
  const from = (msg && msg.from) || (cb && cb.from);
  if (!from || String(from.id) !== String(env.OWNER_CHAT_ID)) return new Response("ok"); // owner lock (v1)
  const uid = from.id;
  const lang = (await snap(env, `${uid}:lang`)) || (from.language_code === "it" ? "it" : "en");
  const S = { ...FALLBACK, ...((await snap(env, `strings:${lang}`)) || {}) };

  if (msg && typeof msg.text === "string") {
    const chat = msg.chat.id, cmd = msg.text.trim().split(/\s+/)[0].toLowerCase().replace(/@.*$/, "");
    if (cmd === "/start") {
      if (!(await snap(env, `${uid}:lang`))) return langPrompt(chat, S, from.language_code);
      return send(chat, S.welcome);
    }
    if (cmd === "/language") return langPrompt(chat, S, lang);
    if (cmd === "/list") return fromSnapshot(chat, await snap(env, `${uid}:list`), S.list_empty);
    if (cmd === "/past") return fromSnapshot(chat, await snap(env, `${uid}:past`), S.past_empty);
    if (cmd === "/help") return send(chat, S.help);
    return send(chat, S.help);
  }

  if (cb) {
    const chat = cb.message.chat.id, mid = cb.message.message_id;
    const [act, arg] = String(cb.data || "").split(":");
    await tg(env, "answerCallbackQuery", { callback_query_id: cb.id }); // stop the spinner at once
    if (act === "st") return fromSnapshot(chat, await snap(env, `${uid}:status:${arg}`), S.list_empty);
    if (act === "hi") {
      const c = await snap(env, `${uid}:chart:${arg}`);
      if (c) await sendPhoto(env, chat, c.png);
      return new Response("ok");
    }
    if (act === "de") return fromSnapshot(chat, await snap(env, `${uid}:delete:${arg}`), S.list_empty);
    if (act === "dn" || act === "dy") {
      const d = await snap(env, `${uid}:delete:${arg}`);
      if (act === "dy") { await enqueue(env, uid, "callback", { data: cb.data }); ctx.waitUntil(dispatch(env, "command")); }
      return Response.json({ method: "editMessageText", chat_id: chat, message_id: mid,
        text: (act === "dy" ? S.deleted : S.kept).replace("{n}", d ? d.n : arg).replace(" ({w})", "") });
    }
    if (act === "lang" && (arg === "en" || arg === "it")) {
      await putSnap(env, `${uid}:lang`, uid, arg);
      await enqueue(env, uid, "lang", { lang: arg });
      const S2 = { ...FALLBACK, ...((await snap(env, `strings:${arg}`)) || {}) };
      return Response.json({ method: "editMessageText", chat_id: chat, message_id: mid, text: S2.lang_set + "\n\n" + S2.welcome });
    }
    // nw (check now), wt (watch this) and anything else: the job does it; answer at once that it's coming
    await enqueue(env, uid, "callback", { data: cb.data });
    ctx.waitUntil(dispatch(env, "command"));
    return send(chat, S.ack);
  }
  return new Response("ok");
}

function send(chat_id, text, kb) {
  // A webhook response may carry one Bot API call: the fastest way to answer.
  return Response.json({ method: "sendMessage", chat_id, text, parse_mode: "HTML", disable_web_page_preview: true,
    ...(kb && kb.length ? { reply_markup: { inline_keyboard: kb } } : {}) });
}

function fromSnapshot(chat, s, empty) {
  return s ? send(chat, s.text, s.kb) : send(chat, empty);
}

function langPrompt(chat, S, preferred) {
  const it = preferred === "it";
  return send(chat, S.lang_q, [[{ text: (it ? "✅ " : "") + "🇮🇹 Italiano", callback_data: "lang:it" }, { text: (it ? "" : "✅ ") + "🇬🇧 English", callback_data: "lang:en" }]]);
}

async function tg(env, method, body) {
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_TOKEN}/${method}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  return r.ok;
}

async function sendPhoto(env, chat_id, b64) {
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const form = new FormData();
  form.append("chat_id", String(chat_id));
  form.append("photo", new Blob([bytes], { type: "image/png" }), "chart.png");
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_TOKEN}/sendPhoto`, { method: "POST", body: form });
  return r.ok;
}

async function snap(env, key) {
  const body = await env.DB.prepare("SELECT body FROM snapshots WHERE key = ?1").bind(key).first("body");
  return body ? JSON.parse(body) : null;
}

async function putSnap(env, key, user_id, value) {
  await env.DB.prepare("INSERT INTO snapshots (key, user_id, updated_at, body) VALUES (?1, ?2, ?3, ?4) " +
    "ON CONFLICT(key) DO UPDATE SET updated_at = ?3, body = ?4").bind(key, user_id, Date.now(), JSON.stringify(value)).run();
}

async function enqueue(env, user_id, kind, payload) {
  await env.DB.prepare("INSERT INTO queue (created_at, user_id, kind, payload) VALUES (?1, ?2, ?3, ?4)")
    .bind(Date.now(), user_id, kind, JSON.stringify(payload)).run();
}

// ---------- API for the GitHub job (Bearer WORKER_SECRET) ----------
async function jobApi(req, env, path) {
  const auth = req.headers.get("Authorization") || "";
  if (!safeEqual(auth, `Bearer ${env.WORKER_SECRET}`)) return new Response("forbidden", { status: 403 });
  if (path === "/job/take" && req.method === "POST") {
    const now = Date.now();
    const { results } = await env.DB.prepare(
      `UPDATE queue SET taken_at = ?1 WHERE id IN (
         SELECT id FROM queue WHERE taken_at IS NULL OR taken_at < ?2 ORDER BY id LIMIT 50)
       RETURNING id, user_id, kind, payload`).bind(now, now - 10 * MIN).all();
    return Response.json(results.sort((a, b) => a.id - b.id));
  }
  if (path === "/job/ack" && req.method === "POST") {
    const { ids } = await req.json();
    await env.DB.prepare("DELETE FROM queue WHERE id IN (SELECT value FROM json_each(?1))").bind(JSON.stringify(ids || [])).run();
    return Response.json({ ok: true });
  }
  if (path === "/job/snapshot" && req.method === "POST") {
    const { rows } = await req.json();
    const now = Date.now();
    const stmt = env.DB.prepare("INSERT INTO snapshots (key, user_id, updated_at, body) VALUES (?1, ?2, ?3, ?4) " +
      "ON CONFLICT(key) DO UPDATE SET updated_at = ?3, body = ?4");
    if (rows && rows.length) await env.DB.batch(rows.map((r) => stmt.bind(r.key, r.user_id, now, r.body)));
    return Response.json({ ok: true, n: (rows || []).length });
  }
  if (path === "/job/enqueue" && req.method === "POST") {   // tooling/tests: queue a command as if it came from Telegram
    const { user_id, kind, payload, dispatch: now } = await req.json();
    await enqueue(env, user_id, kind, payload || {});
    if (now) await dispatch(env, "enqueue");
    return Response.json({ ok: true });
  }
  if (path === "/job/done" && req.method === "POST") {
    await env.DB.prepare("INSERT INTO meta (k, v) VALUES ('last_run_ok', ?1) ON CONFLICT(k) DO UPDATE SET v = ?1").bind(String(Date.now())).run();
    return Response.json({ ok: true });
  }
  return new Response("not found", { status: 404 });
}

// ---------- GitHub dispatch ----------
async function dispatch(env, reason) {
  const base = `https://api.github.com/repos/${env.GH_REPO}/actions/workflows/${env.GH_WORKFLOW}`;
  const headers = { Authorization: `Bearer ${env.GH_TOKEN}`, Accept: "application/vnd.github+json", "User-Agent": "trainwatcher-worker", "X-GitHub-Api-Version": "2022-11-28" };
  for (const status of ["queued", "in_progress"]) {
    const r = await fetch(`${base}/runs?status=${status}&per_page=1`, { headers });
    if (r.ok && (await r.json()).total_count > 0) return false; // a run is already coming; it drains the queue
  }
  const r = await fetch(`${base}/dispatches`, { method: "POST", headers, body: JSON.stringify({ ref: "main", inputs: { reason } }) });
  if (!r.ok) console.log("dispatch failed", r.status);
  return r.ok;
}

function safeEqual(a, b) {
  const x = new TextEncoder().encode(a), y = new TextEncoder().encode(b || "");
  return x.length === y.length && crypto.subtle.timingSafeEqual(x, y);
}
