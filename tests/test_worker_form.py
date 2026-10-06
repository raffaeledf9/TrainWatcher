"""The Worker's /form auth: Telegram initData signed per the Mini App spec is accepted, anything else is not.
Runs the real worker code under Node (skipped if Node is missing)."""
import hashlib
import hmac
import json
import shutil
import subprocess
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path

WORKER = Path(__file__).resolve().parents[1] / "worker" / "src" / "index.js"
TOKEN = "123:FAKE"


def init_data(age=0, tamper=False, token=TOKEN):
    f = {"auth_date": str(int(time.time()) - age), "query_id": "AAx", "user": json.dumps({"id": 42}), "signature": "sig"}
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    f["hash"] = hmac.new(secret, "\n".join(f"{k}={f[k]}" for k in sorted(f)).encode(), hashlib.sha256).hexdigest()
    if tamper:
        f["user"] = json.dumps({"id": 43})
    return urllib.parse.urlencode(f)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class InitData(unittest.TestCase):
    def test_verify(self):
        cases = {"ok": init_data(), "tampered": init_data(tamper=True), "old": init_data(age=90000),
                 "other_bot": init_data(token="123:OTHER"), "no_hash": "auth_date=1&user=%7B%22id%22%3A42%7D"}
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "w.mjs").write_text(WORKER.read_text(encoding="utf-8") + "\nexport { verifyInitData };\n", encoding="utf-8")
            (Path(d) / "t.mjs").write_text(
                "crypto.subtle.timingSafeEqual = (a, b) => Buffer.from(a).equals(Buffer.from(b));\n"  # Cloudflare-only API
                "const { verifyInitData } = await import('./w.mjs');\n"
                f"const c = {json.dumps(cases)}, out = {{}};\n"
                f"for (const k in c) out[k] = await verifyInitData(c[k], {json.dumps(TOKEN)});\n"
                "console.log(JSON.stringify(out));\n", encoding="utf-8")
            out = json.loads(subprocess.run(["node", "t.mjs"], cwd=d, capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out, {"ok": {"id": 42}, "tampered": None, "old": None, "other_bot": None, "no_hash": None})


if __name__ == "__main__":
    unittest.main()
