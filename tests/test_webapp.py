"""R7 Mini App form (webapp/index.html): static checks of the payload contract, the submit call, both languages and
that its fare and class ids are the ones trainwatcher.model accepts."""
import json
import re
import unittest
from pathlib import Path
from urllib.parse import urlparse

from trainwatcher import model

HTML = (Path(__file__).resolve().parent.parent / "webapp" / "index.html").read_text(encoding="utf-8")


def const(name):
    """A JSON-literal `const NAME = {...};` or `const NAME = "...";` from the page script."""
    m = re.search(rf"^const {name} = (\{{.*?\}}|\".*?\");$", HTML, re.M | re.S)
    return json.loads(m.group(1))


class WebappTest(unittest.TestCase):
    def test_payload_has_every_key_from_payload_reads(self):
        body = re.search(r"^function payload\(\) \{\n(.*?)^\}", HTML, re.M | re.S).group(1)
        for key in ("v", "lang", "from", "to", "out", "ret", "pax", "fares", "cls", "ops", "max", "rises", "d", "w"):
            self.assertRegex(body, rf"[{{ ]{key}: ", key)
        self.assertIn("v: 1,", body)

    def test_submit_posts_text_plain_to_the_worker(self):
        self.assertIn('fetch(WORKER + "/form", {method: "POST", headers: {"Content-Type": "text/plain"}', HTML)
        self.assertIn("JSON.stringify({initData: TG.initData, mode: mode, data: payload()})", HTML)
        self.assertIn("TG.close()", HTML)
        url = urlparse(const("WORKER"))
        self.assertEqual(url.scheme, "https")
        self.assertTrue(url.hostname.endswith(".workers.dev"), url.hostname)
        self.assertEqual(url.path, "")
        self.assertEqual(re.findall(r'<script src="([^"]+)"', HTML), ["https://telegram.org/js/telegram-web-app.js"])

    def test_both_languages(self):
        strings = const("STR")
        self.assertEqual(set(strings), {"en", "it"})
        self.assertEqual(set(strings["en"]), set(strings["it"]))
        for k, en, it in [("review", "Review", "Riepilogo"), ("search", "Search", "Cerca"), ("watchT", "Watch", "Monitora"),
                          ("create", "Create watch", "Crea monitoraggio")]:
            self.assertEqual((strings["en"][k], strings["it"][k]), (en, it))
        used = set(re.findall(r'\bt\("(\w+)"\)', HTML))
        self.assertLessEqual(used, set(strings["en"]), "t() keys missing from STR")

    def test_limits_match_the_model(self):
        for name in ("HORIZON_DAYS", "MAX_DAYS"):
            m = re.search(rf"^const {name} = (\d+);", HTML, re.M)
            self.assertEqual(int(m.group(1)), getattr(model, name), name)

    def test_form_enforces_the_horizon_with_a_day_of_margin(self):
        # the server counts from Rome's date; a phone a timezone ahead must not offer a day the server refuses
        self.assertIn("const LAST_DAY = addDays(TODAY, HORIZON_DAYS - 1);", HTML)
        self.assertIn('else if (firstLast(L)[0] > LAST_DAY) e[k] = t("e_far");', HTML)

    def test_fares_and_classes_match_the_model(self):
        self.assertEqual(const("FARES"), model.FARES)
        used = set(re.findall(r'"((?:T|I|TI):[^"]+)"', HTML))
        self.assertLessEqual(used, set(model.FARES))
        classes = [c for cs in const("CLASSES").values() for c in cs]
        self.assertEqual(sorted(classes), sorted(model.CLASSES))
        self.assertEqual(set(const("CLASSES")), set(model.OPERATORS))


if __name__ == "__main__":
    unittest.main()
