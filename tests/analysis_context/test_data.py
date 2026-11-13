from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class DataContextTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / "fixtures/rate_bridge.json").read_text(encoding="utf-8"))

    def test_revisions_and_ambiguous_bridge_are_preserved(self):
        self.assertTrue(any(row["status"] == "withdrawn" for row in self.data["records"]))
        self.assertTrue(any(len(row["targets"]) > 1 for row in self.data["coding_bridges"]))
        self.assertEqual(len(self.data["releases"]), 2)

    def test_exposure_units_and_identity_links(self):
        self.assertAlmostEqual(sum(self.data["standard_population"].values()), 1)
        self.assertTrue(all(row["person_years"] > 0 for row in self.data["exposures"]))
        people = {row["subject_id"] for row in self.data["records"]}
        self.assertTrue(all(row["canonical"] in people and row["alias"] in people for row in self.data["identity_links"]))

