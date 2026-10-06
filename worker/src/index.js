// TrainWatcher Worker: Telegram webhook (instant replies), D1 command queue for the GitHub job,
// on-demand and 5-minute dispatch of the price-check workflow. Talks only to Telegram, GitHub and D1:
// the train operators block Cloudflare (ADR 0001).

const MIN = 60_000;

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

  if (msg && typeof msg.text === "string") {
    const cmd = msg.text.trim().split(/\s+/)[0].toLowerCase();
    if (cmd === "/start") return reply(msg.chat.id, "👋 TrainWatcher is being built. Try /try for a live test search.");
    if (cmd === "/try") {
      await enqueue(env, from.id, "try", {});
      ctx.waitUntil(dispatch(env, "command"));
      return reply(msg.chat.id, "⏳ Got it — checking prices, results in about a minute.");
    }
    return reply(msg.chat.id, "Only /start and /try work in this build.");
  }
  if (cb) {
    await tg(env, "answerCallbackQuery", { callback_query_id: cb.id });
    await enqueue(env, from.id, "callback", { data: cb.data, message_id: cb.message && cb.message.message_id });
    ctx.waitUntil(dispatch(env, "command"));
  }
  return new Response("ok");
}

// A webhook response may carry one Bot API call: the fastest way to answer a command.
function reply(chat_id, text) {
  return Response.json({ method: "sendMessage", chat_id, text });
}

async function tg(env, method, body) {
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_TOKEN}/${method}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  return r.ok;
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
