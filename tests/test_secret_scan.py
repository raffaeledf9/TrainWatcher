import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import secret_scan  # noqa: E402


class SecretScanTest(unittest.TestCase):
    def hits(self, text, secrets=None):
        out = []
        secret_scan.scan_text("f", text, secrets or {}, out)
        return out

    def test_token_shapes_are_caught(self):
        self.assertTrue(self.hits('t = "123456789:' + "A" * 35 + '"'))
        self.assertTrue(self.hits("github_pat_" + "a" * 30))
        self.assertTrue(self.hits('oauth_token = "' + "x" * 30 + '"'))

    def test_env_values_are_caught_without_printing_them(self):
        out = self.hits("prefix supersecretvalue suffix", {"GH_TOKEN": "supersecretvalue"})
        self.assertEqual(out, ["f:1: value of .env GH_TOKEN"])
        self.assertNotIn("supersecretvalue", out[0])

    def test_ordinary_text_is_clean(self):
        self.assertEqual(self.hits("price 29.90 at 06:15, train 9611"), [])


if __name__ == "__main__":
    unittest.main()
