"""The invite flow in the real Worker code (Node, with node:sqlite standing in for D1 and fake Telegram/GitHub):
one link, one notification per person, allow / decline / remove, friends' rate limit, private chats only."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_worker_form import init_data

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r"""
import { DatabaseSync } from "node:sqlite";
import { readFileSync } from "fs";
crypto.subtle.timingSafeEqual = (a, b) => Buffer.from(a).equals(Buffer.from(b));
const sql = new DatabaseSync(":memory:");
sql.exec(readFileSync(process.argv[2], "utf8"));
const D1 = { prepare(q) { const s = { args: [], bind(...a) { s.args = a; return s; },
  first: async (col) => { const r = sql.prepare(q).get(...s.args); return r ? (col ? r[col] : r) : null; },
  run: async () => ({ meta: { changes: Number(sql.prepare(q).run(...s.args).changes) } }),
  all: async () => ({ results: sql.prepare(q).all(...s.args) }) }; return s; },
  batch: async (list) => Promise.all(list.map((s) => s.run())) };
let calls = [], getMeDown = false;
globalThis.fetch = async (url, opts = {}) => {
  const method = String(url).split("/").pop();
  if (String(url).includes("api.telegram.org")) {
    calls.push({ method, body: opts.body && typeof opts.body === "string" ? JSON.parse(opts.body) : {} });
    if (method === "getMe" && getMeDown) return new Response("Bad Gateway", { status: 502 });
    return Response.json({ ok: true, result: method === "getMe" ? { username: "tr4inw4tcher_bot" } : true });
  }
  if (String(url).includes("/runs?")) return Response.json({ total_count: 0 });
  return new Response(null, { status: 204 });                         // GitHub dispatch
};
const worker = (await import(process.argv[3])).default;
const env = { DB: D1, TG_SECRET: "s", TELEGRAM_TOKEN: "123:FAKE", OWNER_CHAT_ID: "1", GH_TOKEN: "g", GH_REPO: "x/y", GH_WORKFLOW: "run.yml" };
const steps = JSON.parse(readFileSync(process.argv[4], "utf8")), out = [];
let code = null;
for (const st of steps) {
  calls = []; const pending = []; getMeDown = !!st.getMeDown;
  const ctx = { waitUntil: (p) => pending.push(p) };
  let req;
  if (st.form) req = new Request("https://w.dev/form", { method: "POST", body: JSON.stringify({ initData: st.form, mode: "search", data: { v: 1 } }) });
  else {
    const chat = { id: st.chat || st.from, type: st.type || "private" };
    const from = { id: st.from, first_name: st.name || "Mario", last_name: "Rossi", username: "mario", language_code: "it" };
    const text = (st.text || "").replace("{code}", code).replace("{old}", st.oldCode || "");
    const update = st.cb ? { callback_query: { id: "q", from, data: st.cb, message: { chat, message_id: 9 } } } : { message: { chat, from, text } };
    req = new Request("https://w.dev/tg", { method: "POST", headers: { "X-Telegram-Bot-Api-Secret-Token": "s" }, body: JSON.stringify(update) });
  }
  const res = await worker.fetch(req, env, ctx);
  await Promise.all(pending);
  const body = await res.text();
  let reply = null; try { reply = JSON.parse(body); } catch {}
  const m = (reply && reply.text || "").match(/start=([\w-]+)/);
  if (m) code = m[1];
  out.push({ status: res.status, reply, calls: calls.filter((c) => c.method !== "answerCallbackQuery"),
             users: sql.prepare("SELECT id, status FROM users ORDER BY id").all().map((r) => [r.id, r.status]),
             invite: (sql.prepare("SELECT v FROM meta WHERE k = 'invite_code'").get() || {}).v,
             queue: sql.prepare("SELECT kind, payload FROM queue").all().map((r) => [r.kind, JSON.parse(r.payload)]) });
}
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "node not installed")
class Invites(unittest.TestCase):
    def run_steps(self, steps):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "h.mjs").write_text(HARNESS, encoding="utf-8")
            Path(d, "w.mjs").write_text((ROOT / "worker" / "src" / "index.js").read_text(encoding="utf-8"), encoding="utf-8")
            Path(d, "steps.json").write_text(json.dumps(steps), encoding="utf-8")
            r = subprocess.run(["node", "h.mjs", str(ROOT / "worker" / "schema.sql"), Path(d, "w.mjs").as_uri(), "steps.json"],
                               cwd=d, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            return json.loads(r.stdout.strip().splitlines()[-1])  # the Worker may log errors on lines before it

    def test_flow(self):
        friend = init_data()  # user 42
        steps = [
            {"from": 42, "text": "/start"},                        # 0 stranger without the link: silence
            {"from": 1, "text": "/invite"},                        # 1 the Owner gets the link
            {"from": 42, "text": "/start wrong"},                  # 2 a wrong code: silence
            {"from": 42, "text": "/start {code}"},                 # 3 the request: the Owner is told who
            {"from": 42, "text": "/start {code}"},                 # 4 again: "still waiting", no second notice
            {"from": 42, "text": "/list"},                         # 5 pending: silence
            {"from": 1, "cb": "fa:42"},                            # 6 the Owner allows
            {"from": 42, "text": "/list"},                         # 7 now answered
            *[{"form": friend}] * 13,                              # 8-20 the 13th form in an hour is refused
            {"from": 1, "text": "/friends"},                       # 21
            {"from": 1, "cb": "fr:42"},                            # 22 the Owner removes them
            {"from": 42, "text": "/list"},                         # 23 silence again
            {"from": 42, "text": "/start {code}"},                 # 24 removed: no new request, no notice
            {"from": 1, "text": "/invite reset"},                  # 25 a new link...
            {"from": 43, "name": "Luca", "text": "/start {old}", "oldCode": "PLACEHOLDER"},  # 26 ...the old one is dead
            {"from": 43, "name": "Luca", "text": "/start {code}"},  # 27 ...the new one works
            {"from": 1, "chat": -100, "type": "group", "text": "/list"},  # 28 groups: silence
            {"from": 1, "text": "/help"},                          # 29 the Owner's help lists /invite and /friends
            {"from": 1, "cb": "fa:43"},                            # 30 the Owner allows Luca...
            {"from": 1, "cb": "fa:43"},                            # 31 ...taps the old Allow again: nothing new
            {"from": 1, "cb": "fd:43"},                            # 32 ...and Deny on the old request: that removes him
            {"from": 1, "text": "/start"},                         # 33 the Owner's "/" menu includes /invite and /friends
        ]
        first = self.run_steps(steps[:2])
        steps[26]["oldCode"] = first[1]["reply"]["text"].split("start=")[1].split()[0]
        out = self.run_steps(steps)
        replies = [o["reply"]["text"] if o["reply"] and "text" in o["reply"] else None for o in out]
        self.assertIsNone(replies[0])
        self.assertIn("https://t.me/tr4inw4tcher_bot?start=", replies[1])
        self.assertIsNone(replies[2])
        self.assertIn("Request sent", replies[3])
        notice = [c for c in out[3]["calls"] if c["body"].get("chat_id") == "1"]
        self.assertEqual(len(notice), 1)
        self.assertIn("Mario Rossi", notice[0]["body"]["text"])
        self.assertIn("@mario", notice[0]["body"]["text"])
        self.assertIn("id <code>42</code>", notice[0]["body"]["text"])
        self.assertEqual([b["callback_data"] for b in notice[0]["body"]["reply_markup"]["inline_keyboard"][0]], ["fa:42", "fd:42"])
        self.assertIn("still waiting", replies[4])
        self.assertFalse([c for c in out[4]["calls"] if c["body"].get("chat_id") == "1"])
        self.assertIsNone(replies[5])
        self.assertIn("can now use the bot", replies[6])
        self.assertIn([42, "allowed"], out[6]["users"])
        self.assertTrue(any(c["method"] == "setChatMenuButton" and c["body"]["chat_id"] == 42 for c in out[6]["calls"]))
        self.assertIn(["lang", {"lang": "it"}], out[6]["queue"])     # their Telegram language becomes theirs here too
        self.assertIn("No watches yet", replies[7])
        self.assertEqual([o["status"] for o in out[8:21]], [200] * 12 + [429])
        self.assertIn("Mario Rossi (@mario) — allowed", replies[21])
        self.assertIn("removed", replies[22])
        self.assertIn(["revoke", {"user_id": 42}], out[22]["queue"])
        self.assertIsNone(replies[23])
        self.assertIsNone(replies[24])
        self.assertFalse([c for c in out[24]["calls"] if c["body"].get("chat_id") == "1"])
        self.assertIn("New invite link", replies[25])
        self.assertIsNone(replies[26])
        self.assertIn("Request sent", replies[27])
        self.assertIsNone(replies[28])
        self.assertIn("/invite", replies[29])
        self.assertEqual(sum(c["method"] == "sendMessage" and c["body"]["chat_id"] == 43 for c in out[30]["calls"]), 1)
        self.assertFalse([c for c in out[31]["calls"] if c["body"].get("chat_id") == 43])  # no second welcome
        self.assertIn([43, "removed"], out[32]["users"])
        self.assertIn(["revoke", {"user_id": 43}], out[32]["queue"])                       # his watches stop too
        cmds = lambda o, chat: [[c["command"] for c in x["body"]["commands"]] for x in o["calls"] if x["method"] == "setMyCommands" and x["body"]["scope"]["chat_id"] == chat]
        self.assertEqual(cmds(out[6], 42), [["list", "past", "language", "help"]])         # a friend's menu, no Owner commands
        self.assertEqual(cmds(out[33], 1), [["list", "past", "language", "help", "invite", "friends"]])


class NamesAreShownAsTyped(Invites):
    def test_flow(self):
        pass

    def test_dollar_patterns_in_a_name(self):  # String.replace expands $$ $& $` $' in a replacement string
        name = "Cash$$ $& $` $'"
        out = self.run_steps([{"from": 1, "text": "/invite"}, {"from": 44, "name": name, "text": "/start {code}"},
                              {"from": 1, "cb": "fa:44"}, {"from": 1, "text": "/friends"}])
        notice = [c for c in out[1]["calls"] if c["body"].get("chat_id") == "1"][0]["body"]["text"]
        html = name.replace("&", "&amp;")
        self.assertIn(f"<b>{html} Rossi</b>", notice)
        self.assertIn(f"<b>{html} Rossi</b> can now use the bot", out[2]["reply"]["text"])
        self.assertEqual(out[3]["reply"]["reply_markup"]["inline_keyboard"][0][0]["text"], f"🚫 Remove {name} Rossi")


class InviteWhileTelegramIsDown(Invites):
    def test_flow(self):
        pass

    def test_reset_changes_nothing_if_the_link_cannot_be_shown(self):
        out = self.run_steps([{"from": 1, "text": "/invite reset", "getMeDown": True},   # Telegram down, name not known yet
                              {"from": 1, "text": "/invite"},
                              {"from": 1, "text": "/invite reset", "getMeDown": True}])  # the name is remembered now
        self.assertIsNone(out[0].get("invite"))                               # nothing created...
        self.assertIn("try again", out[0]["reply"]["text"])                   # ...and the Owner is told, not left in silence
        self.assertIn(out[1]["invite"], out[1]["reply"]["text"])
        self.assertNotEqual(out[2]["invite"], out[1]["invite"])               # a reset works without asking Telegram again
        self.assertIn(out[2]["invite"], out[2]["reply"]["text"])              # and always shows the link it made


if __name__ == "__main__":
    unittest.main()
