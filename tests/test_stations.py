import unittest

from trainwatcher import stations
from trainwatcher.offers import ITALO, TRENITALIA


class StationsTest(unittest.TestCase):
    def ids(self, key):
        return stations.operator_ids(stations.by_key(key))

    def test_known_stations_resolve_on_both_operators(self):
        for key, t, i in [("milano-centrale", 830001700, "MC_"), ("roma-termini", 830008409, "RMT"),
                          ("napoli-centrale", 830009218, "NAC"), ("firenze-s-m-novella", 830006421, "SMN"),
                          ("bologna-centrale", 830005043, "BC_")]:
            self.assertEqual(self.ids(key), {TRENITALIA: t, ITALO: i}, key)

    def test_city_groups(self):
        self.assertEqual(self.ids("milano-tutte"), {TRENITALIA: 830001650, ITALO: "MI0"})
        self.assertEqual(self.ids("roma-tutte"), {TRENITALIA: 830008349, ITALO: "RM0"})
        groups = [e for e in stations.entries() if e.get("g")]
        self.assertGreaterEqual(len(groups), 11)
        self.assertEqual(stations.entries()[:len(groups)], groups, "groups come first")

    def test_italo_only_stations_have_no_trenitalia_id(self):
        for key in ("aversa", "bisceglie", "molfetta", "trani"):
            e = stations.by_key(key)
            self.assertIsNone(e["t"], key)
            self.assertEqual(list(stations.operator_ids(e)), [ITALO])

    def test_keys_unique_and_abbreviations_present(self):
        es = stations.entries()
        self.assertEqual(len({e["k"] for e in es}), len(es))
        self.assertTrue(all(len(stations.abbreviation(e)) == 3 and e["a"].isupper() for e in es))
        self.assertTrue(all(e["t"] or e["i"] for e in es))
        self.assertEqual({stations.abbreviation(stations.by_key(k)) for k in
                          ("milano-tutte", "milano-centrale", "milano-rogoredo")}, {"MIL"})
        self.assertNotEqual(stations.by_key("reggio-emilia")["a"], stations.by_key("reggio-calabria")["a"])

    def test_search_is_for_tools_only(self):
        self.assertIn("roma-termini", [e["k"] for e in stations.search("term")])
        self.assertEqual([e["k"] for e in stations.search("FORLI")], ["forli"])
        with self.assertRaises(KeyError):
            stations.by_key("Milano Centrale")

    def test_file_size(self):
        self.assertLessEqual(stations.PATH.stat().st_size, 15 * 1024)


if __name__ == "__main__":
    unittest.main()
