// TrainWatcher Worker: Telegram webhook with instant replies from snapshots (ADR 0003), D1 command queue for the
// GitHub job, on-demand and 5-minute dispatch of the price-check workflow. Talks only to Telegram, GitHub and D1:
// the train operators block Cloudflare (ADR 0001).

const MIN = 60_000;
// Fallback strings until the job has published strings:<lang> to D1 (the job's i18n.py is the source of truth).
const FALLBACK = { ack: "⏳ Got it — checking prices, results in about a minute.", list_empty: "No watches yet.", past_empty: "No past watches yet.",
  welcome: "Welcome to TrainWatcher 👋", lang_q: "Choose your language:", help: "/list · /past · /language", deleted: "🗑 Watch {n} deleted.",
  kept: "↩️ Watch {n} kept.", del_yes: "✅ Delete", del_no: "↩️ Keep", lang_set: "OK",
  job_dead: "⚠️ Price checks have stopped finishing.", recovered: "✅ Price checks are working again.",
  dispatch_fail: "⚠️ GitHub refused to start a price check: the GitHub token has probably expired.", no_chart: "📈 No price history yet.",
  req_sent: "📨 Request sent. You'll get a message here once it's approved.", req_wait: "⏳ Your request is still waiting for approval.",
  req_new: "👤 <b>Access request</b>\n{who}", btn_allow: "✅ Allow", btn_deny: "❌ Deny", btn_remove: "🚫 Remove {name}",
  btn_readd: "✅ Allow {name} again", allowed_owner: "✅ {name} can now use the bot.", denied_owner: "❌ {name} declined.",
  removed_owner: "🚫 {name} removed; their watches are stopped.", allowed_friend: "✅ You're in!", removed_friend: "Your access to TrainWatcher has ended.",
  invite_text: "🔗 Invite link:\n{link}", invite_reset: "🔄 New invite link (the old one no longer works):\n{link}", friends_t: "👥 Friends",
  friends_empty: "No friends yet: share the link from /invite.", st_pending: "waiting", st_allowed: "allowed", st_denied: "declined",
  st_removed: "removed", help_owner: "/invite · /friends", rate_limited: "⏳ Too many requests in the last hour: try again later." };

export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    try {
      if (req.method === "POST" && url.pathname === "/tg") return await telegram(req, env, ctx);
      if (req.method === "POST" && url.pathname === "/form") return await form(req, env, ctx);
      if (url.pathname.startsWith("/job/")) return await jobApi(req, env, url.pathname);
      return new Response("TrainWatcher", { status: 200 });
    } catch (e) {
      console.log("error", url.pathname, String(e).slice(0, 200));
      // Telegram re-delivers non-2xx updates, so the webhook always answers 200.
      return url.pathname === "/tg" ? new Response("ok") : new Response("error", { status: 500 });
    }
  },

  async scheduled(event, env, ctx) {
    const minute = new Date(event.scheduledTime).getUTCMinutes(), now = Date.now();
    const pending = await env.DB.prepare("SELECT COUNT(*) AS n FROM queue WHERE taken_at IS NULL").first("n");
    const due = await snap(env, "next_due"); // earliest next check of any watch (ms), published by the job; null = none
    const { results } = await env.DB.prepare("SELECT k, v FROM meta WHERE k IN ('last_run_ok', 'pending_since')").all();
    const m = Object.fromEntries(results.map((r) => [r.k, r.v]));
    const d = decide(m, pending, due, minute, now);
    if (d) await dispatch(env, d);
    await notifyOnChange(env, "job_dead", jobDead(m, now));
  },
};

// ---------- Telegram webhook ----------
// Who may use the bot: the Owner, and friends the Owner allowed after they opened the one invite link
// (users.status: pending | allowed | denied | removed). Everyone else gets silence. Private chats only.
async function telegram(req, env, ctx) {
  if (!safeEqual(req.headers.get("X-Telegram-Bot-Api-Secret-Token") || "", env.TG_SECRET)) return new Response("ok");
  const u = await req.json();
  const msg = u.message, cb = u.callback_query;
  const from = (msg && msg.from) || (cb && cb.from);
  const chatType = msg ? msg.chat.type : cb && cb.message ? cb.message.chat.type : "private";
  if (!from || chatType !== "private") return new Response("ok");
  const uid = from.id, origin = new URL(req.url).origin, role = await roleOf(env, uid);
  if (role !== "owner" && role !== "allowed") return msg && typeof msg.text === "string" ? await joinRequest(env, msg, role) : new Response("ok");
  const owner = role === "owner";
  const lang = (await snap(env, `${uid}:lang`)) || (from.language_code === "it" ? "it" : "en");
  const S = await strings(env, lang);

  if (msg && typeof msg.text === "string") {
    const chat = msg.chat.id, [word, arg] = msg.text.trim().split(/\s+/), cmd = word.toLowerCase().replace(/@.*$/, "");
    if (cmd === "/start") {
      if (!(await snap(env, `${uid}:lang`))) return langPrompt(chat, S, from.language_code);
      ctx.waitUntil(menu(env, origin, chat, lang));
      return send(chat, S.welcome);
    }
    if (cmd === "/language") return langPrompt(chat, S, lang);
    if (cmd === "/list") return fromSnapshot(env, chat, await snap(env, `${uid}:list`), S.list_empty);
    if (cmd === "/past") return fromSnapshot(env, chat, await snap(env, `${uid}:past`), S.past_empty);
    if (owner && cmd === "/invite") return await invite(env, chat, S, arg === "reset");
    if (owner && cmd === "/friends") return await friends(env, chat, S);
    return send(chat, S.help + (owner ? "\n" + S.help_owner : ""));
  }

  if (cb) {
    const chat = cb.message.chat.id, mid = cb.message.message_id;
    const [act, arg] = String(cb.data || "").split(":");
    await tg(env, "answerCallbackQuery", { callback_query_id: cb.id }); // stop the spinner at once
    if (owner && ["fa", "fd", "fr"].includes(act)) return await decideFriend(env, ctx, origin, act, arg, chat, mid, S);
    if (act === "st") return fromSnapshot(env, chat, await snap(env, `${uid}:status:${arg}`), S.list_empty);
    if (act === "hi") {
      const c = await snap(env, `${uid}:chart:${arg}`);
      if (!c) return send(chat, S.no_chart);
      await sendPhoto(env, chat, c.png);
      return new Response("ok");
    }
    if (act === "de") return fromSnapshot(env, chat, await snap(env, `${uid}:delete:${arg}`), S.list_empty);
    if (act === "dn" || act === "dy") {
      const d = await snap(env, `${uid}:delete:${arg}`);
      if (act === "dy") { await enqueue(env, uid, "callback", { data: cb.data }); ctx.waitUntil(dispatch(env, "command")); }
      return Response.json({ method: "editMessageText", chat_id: chat, message_id: mid,
        text: (act === "dy" ? S.deleted : S.kept).replace("{n}", d ? d.n : arg).replace(" ({w})", "") });
    }
    if (act === "lang" && (arg === "en" || arg === "it")) {
      await putSnap(env, `${uid}:lang`, uid, arg);
      await enqueue(env, uid, "lang", { lang: arg });
      ctx.waitUntil(menu(env, origin, chat, arg));
      const S2 = await strings(env, arg);
      return Response.json({ method: "editMessageText", chat_id: chat, message_id: mid, text: S2.lang_set + "\n\n" + S2.welcome });
    }
    // nw (check now), wt (watch this) and anything else: the job does it; answer at once that it's coming
    if (!owner && (act === "nw" || act === "wt") && (await overLimit(env, uid))) return send(chat, S.rate_limited);
    await enqueue(env, uid, "callback", { data: cb.data });
    ctx.waitUntil(dispatch(env, "command"));
    return send(chat, S.ack);
  }
  return new Response("ok");
}

async function roleOf(env, uid) {
  if (String(uid) === String(env.OWNER_CHAT_ID)) return "owner";
  return (await env.DB.prepare("SELECT status FROM users WHERE id = ?1").bind(uid).first("status")) || null;
}

async function strings(env, lang) {
  return { ...FALLBACK, ...((await snap(env, `strings:${lang}`)) || {}) };
}

const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const fullName = (f) => [f.first_name, f.last_name].filter(Boolean).join(" ") || "?";

// ---------- invites: one link for everyone; each person asks once, the Owner allows or declines ----------
async function joinRequest(env, msg, status) {
  const [cmd, code] = msg.text.trim().split(/\s+/), invite = await metaGet(env, "invite_code");
  if (cmd !== "/start" || !code || !invite || !safeEqual(code, invite)) return new Response("ok"); // strangers: silence
  const f = msg.from, lang = f.language_code === "it" ? "it" : "en", S = await strings(env, lang);
  if (status === "pending") return send(msg.chat.id, S.req_wait);
  if (status) return new Response("ok");                    // declined or removed: no new request
  const r = await env.DB.prepare("INSERT OR IGNORE INTO users (id, status, name, username, lang, requested_at) VALUES (?1, 'pending', ?2, ?3, ?4, ?5)")
    .bind(f.id, fullName(f), f.username || null, lang, Date.now()).run();
  if (!r.meta.changes) return send(msg.chat.id, S.req_wait);  // a double tap: notify the Owner only once
  const O = await strings(env, (await snap(env, `${env.OWNER_CHAT_ID}:lang`)) || "en");
  const who = `<b>${esc(fullName(f))}</b>` + (f.username ? ` · @${esc(f.username)}` : "") + ` · id <code>${f.id}</code>` + (f.language_code ? ` · ${esc(f.language_code)}` : "");
  await tg(env, "sendMessage", { chat_id: env.OWNER_CHAT_ID, parse_mode: "HTML", text: O.req_new.replace("{who}", who),
    reply_markup: { inline_keyboard: [[{ text: O.btn_allow, callback_data: `fa:${f.id}` }, { text: O.btn_deny, callback_data: `fd:${f.id}` }]] } });
  return send(msg.chat.id, S.req_sent);
}

// fa: allow (also again after a removal), fd: decline, fr: remove (the job stops their watches). Buttons of old
// messages can be tapped again: a decision already taken changes nothing, and declining someone already allowed
// (an old request message) is a removal, so their watches stop too.
async function decideFriend(env, ctx, origin, act, arg, chat, mid, S) {
  const row = await env.DB.prepare("SELECT id, name, lang, status FROM users WHERE id = ?1").bind(Number(arg)).first();
  if (!row) return new Response("ok");
  let status = { fa: "allowed", fd: "denied", fr: "removed" }[act];
  if (status === "denied" && row.status === "allowed") status = "removed";
  const changed = status !== row.status;
  if (changed) await env.DB.prepare("UPDATE users SET status = ?1, decided_at = ?2 WHERE id = ?3").bind(status, Date.now(), row.id).run();
  const F = await strings(env, (await snap(env, `${row.id}:lang`)) || row.lang || "en");
  if (!changed) {
    // nothing to do
  } else if (status === "allowed") {
    const lang = row.lang || "en";  // their Telegram language until they pick one: the job writes to them in it too
    await putSnap(env, `${row.id}:lang`, row.id, lang);
    await enqueue(env, row.id, "lang", { lang });
    await tg(env, "sendMessage", { chat_id: row.id, text: F.allowed_friend + "\n\n" + F.lang_q, reply_markup: { inline_keyboard: langKb(row.lang) } });
    await menu(env, origin, row.id, row.lang || "en");
  } else if (status === "removed" && row.status === "allowed") {
    await enqueue(env, Number(env.OWNER_CHAT_ID), "revoke", { user_id: row.id });
    ctx.waitUntil(dispatch(env, "command"));
    await tg(env, "sendMessage", { chat_id: row.id, text: F.removed_friend });
    await tg(env, "setChatMenuButton", { chat_id: row.id, menu_button: { type: "default" } });
  }
  const done = { allowed: S.allowed_owner, denied: S.denied_owner, removed: S.removed_owner }[status];
  return Response.json({ method: "editMessageText", chat_id: chat, message_id: mid, parse_mode: "HTML", text: done.replace("{name}", `<b>${esc(row.name)}</b>`) });
}

async function invite(env, chat, S, reset) {
  let code = await metaGet(env, "invite_code");
  if (!code || reset) {
    code = btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(12)))).replace(/\+/g, "-").replace(/\//g, "_");
    await metaPut(env, "invite_code", code);
  }
  const me = await (await fetch(`https://api.telegram.org/bot${env.TELEGRAM_TOKEN}/getMe`)).json();
  return send(chat, (reset ? S.invite_reset : S.invite_text).replace("{link}", `https://t.me/${me.result.username}?start=${code}`));
}

async function friends(env, chat, S) {
  const { results } = await env.DB.prepare("SELECT id, status, name, username FROM users ORDER BY requested_at DESC LIMIT 30").all();
  if (!results.length) return send(chat, S.friends_empty);
  const text = S.friends_t + "\n\n" + results.map((r) => `• ${esc(r.name)}${r.username ? " (@" + esc(r.username) + ")" : ""} — ${S["st_" + r.status]}`).join("\n");
  const kb = results.map((r) => r.status === "allowed" ? [{ text: S.btn_remove.replace("{name}", r.name), callback_data: `fr:${r.id}` }]
    : r.status === "pending" ? [{ text: `${S.btn_allow} ${r.name}`, callback_data: `fa:${r.id}` }, { text: S.btn_deny, callback_data: `fd:${r.id}` }]
    : [{ text: S.btn_readd.replace("{name}", r.name), callback_data: `fa:${r.id}` }]);
  return send(chat, text, kb);
}

// Friends: at most RATE searches, new watches and "check now"s an hour (they share the Owner's capacity).
const RATE = 12;
async function overLimit(env, uid) {
  const key = `rl:${uid}`, now = Date.now();
  const recent = JSON.parse((await metaGet(env, key)) || "[]").filter((t) => now - t < 60 * MIN);
  if (recent.length >= RATE) return true;
  await metaPut(env, key, JSON.stringify([...recent, now]));
  return false;
}

async function metaGet(env, k) {
  return env.DB.prepare("SELECT v FROM meta WHERE k = ?1").bind(k).first("v");
}

async function metaPut(env, k, v) {
  await env.DB.prepare("INSERT INTO meta (k, v) VALUES (?1, ?2) ON CONFLICT(k) DO UPDATE SET v = ?2").bind(k, v).run();
}

// The menu button opens the Mini App form (served from this Worker's assets) in the user's language.
function menu(env, origin, chat_id, lang) {
  return tg(env, "setChatMenuButton", { chat_id, menu_button: { type: "web_app", text: "🚄 TrainWatcher", web_app: { url: `${origin}/?lang=${lang}` } } });
}

// ---------- Mini App form ----------
// A menu-button Mini App can't use sendData, so the form POSTs here (text/plain, same origin); Telegram's signed
// initData proves who sent it. The job validates the form itself (model.from_payload).
async function form(req, env, ctx) {
  const body = await req.text();
  if (body.length > 16_000) return Response.json({ ok: false, error: "too large" }, { status: 413 });
  let p;
  try { p = JSON.parse(body); } catch { return Response.json({ ok: false, error: "bad request" }, { status: 400 }); }
  const user = await verifyInitData(String(p.initData || ""), env.TELEGRAM_TOKEN);
  const role = user && (await roleOf(env, user.id));
  if (role !== "owner" && role !== "allowed") return Response.json({ ok: false, error: "not allowed" }, { status: 403 });
  if (!["watch", "search"].includes(p.mode) || !p.data || typeof p.data !== "object") return Response.json({ ok: false, error: "bad request" }, { status: 400 });
  const S = await strings(env, (await snap(env, `${user.id}:lang`)) || "en");
  if (role === "allowed" && (await overLimit(env, user.id))) return Response.json({ ok: false, error: S.rate_limited }, { status: 429 });
  await enqueue(env, user.id, "form", { mode: p.mode, data: p.data });
  ctx.waitUntil(Promise.all([dispatch(env, "form"), tg(env, "sendMessage", { chat_id: user.id, text: S.ack })]));
  return Response.json({ ok: true });
}

// https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
async function verifyInitData(initData, token, maxAgeS = 86400) {
  const q = new URLSearchParams(initData), hash = q.get("hash") || "";
  q.delete("hash");
  const check = [...q.entries()].sort(([a], [b]) => (a < b ? -1 : 1)).map(([k, v]) => `${k}=${v}`).join("\n");
  const enc = new TextEncoder();
  const hmac = async (key, msg) => new Uint8Array(await crypto.subtle.sign("HMAC",
    await crypto.subtle.importKey("raw", key, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]), enc.encode(msg)));
  const mine = [...(await hmac(await hmac(enc.encode("WebAppData"), token), check))].map((b) => b.toString(16).padStart(2, "0")).join("");
  if (!hash || !safeEqual(mine, hash) || Date.now() / 1000 - Number(q.get("auth_date")) > maxAgeS) return null;
  try { return JSON.parse(q.get("user")); } catch { return null; }
}

function sendBody(chat_id, text, kb) {
  return { chat_id, text, parse_mode: "HTML", disable_web_page_preview: true, ...(kb && kb.length ? { reply_markup: { inline_keyboard: kb } } : {}) };
}

function send(chat_id, text, kb) {
  // A webhook response may carry one Bot API call: the fastest way to answer.
  return Response.json({ method: "sendMessage", ...sendBody(chat_id, text, kb) });
}

// A snapshot is one message, or several (a period spanning months, one per month): those go out in order through
// the API, since a webhook reply can carry only one.
async function fromSnapshot(env, chat, s, empty) {
  if (!s) return send(chat, empty);
  const parts = s.parts || [{ text: s.text, kb: s.kb }];
  if (parts.length === 1) return send(chat, parts[0].text, parts[0].kb);
  for (const p of parts) await tg(env, "sendMessage", sendBody(chat, p.text, p.kb));
  return new Response("ok");
}

function langKb(preferred) {
  const it = preferred === "it";
  return [[{ text: (it ? "✅ " : "") + "🇮🇹 Italiano", callback_data: "lang:it" }, { text: (it ? "" : "✅ ") + "🇬🇧 English", callback_data: "lang:en" }]];
}

function langPrompt(chat, S, preferred) {
  return send(chat, S.lang_q, langKb(preferred));
}

async function tg(env, method, body) {
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_TOKEN}/${method}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  return r.ok;
}

async function sendPhoto(env, chat_id, b64) {
  // a plain loop: Uint8Array.from with a callback takes 8-20 ms of CPU on a chart, over the free plan's 10 ms
  const bin = atob(b64), bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
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
    await env.DB.batch([
      env.DB.prepare("INSERT INTO meta (k, v) VALUES ('last_run_ok', ?1) ON CONFLICT(k) DO UPDATE SET v = ?1").bind(String(Date.now())),
      env.DB.prepare("DELETE FROM meta WHERE k = 'pending_since'")]);
    return Response.json({ ok: true });
  }
  return new Response("not found", { status: 404 });
}

// Which run to start this minute, if any. Runs start only when there is work: a queued command (at once), a due
// watch (checked every 5 minutes), or a heartbeat after 2 hours without a finished run (GitHub disables the hourly
// fallback schedule of a repo without activity). While a started run hasn't finished for 10 minutes the job is
// failing: retry every 30 minutes instead of flooding GitHub with failing runs.
function decide(m, pending, due, minute, now) {
  const stuck = m.pending_since && now - Number(m.pending_since) > 10 * MIN;
  const slot = stuck ? minute % 30 === 0 : minute % 5 === 0;
  if (pending > 0 && (!stuck || slot)) return "queue";
  if (!slot) return null;
  if (due && now >= due) return "due";
  if (m.last_run_ok && now - Number(m.last_run_ok) > 120 * MIN) return "heartbeat";
  return null;
}

// Dead-man switch (map ticket 13): tell the Owner when runs stop finishing, and when they are back. Runs start only
// when there is work, so silence alone is normal: alarm when a run this Worker started hasn't finished within
// 30 minutes, or when nothing (not even GitHub's hourly fallback run) has finished for 3 hours.
// The job watches this Worker in turn (run.py health).
function jobDead(m, now) {
  if (!m.last_run_ok) return false;
  return Boolean(m.pending_since && now - Number(m.pending_since) > 30 * MIN) || now - Number(m.last_run_ok) > 180 * MIN;
}

// One Owner notice when a problem starts (S[key]) and one when it ends; quiet at night like the alerts.
async function notifyOnChange(env, key, bad) {
  if (bad === ((await metaGet(env, key)) === "1")) return;
  const uid = env.OWNER_CHAT_ID, S = await strings(env, (await snap(env, `${uid}:lang`)) || "en");
  const h = Number(new Intl.DateTimeFormat("en", { hour: "numeric", hourCycle: "h23", timeZone: "Europe/Rome" }).format(new Date()));
  if (await tg(env, "sendMessage", { chat_id: uid, text: bad ? S[key] : S.recovered, disable_notification: h >= 23 || h < 7 }))
    await metaPut(env, key, bad ? "1" : "");
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
  if (r.ok) await env.DB.prepare("INSERT INTO meta (k, v) VALUES ('pending_since', ?1) ON CONFLICT(k) DO NOTHING").bind(String(Date.now())).run();
  else console.log("dispatch failed", r.status);
  // 401/403/404: the token expired or lost access to the repo; other errors are GitHub hiccups, retried next minute
  if (r.ok || [401, 403, 404].includes(r.status)) await notifyOnChange(env, "dispatch_fail", !r.ok);
  return r.ok;
}

function safeEqual(a, b) {
  const x = new TextEncoder().encode(a), y = new TextEncoder().encode(b || "");
  return x.length === y.length && crypto.subtle.timingSafeEqual(x, y);
}
