from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class DataContextTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / "fixtures/completeness.json").read_text())

    def test_opportunity_contract_covers_all_appearances(self):
        active = {(row["region"], row["window"], row["source"]) for row in self.data["opportunities"] if row["active"]}
        self.assertTrue(all((row["region"], row["window"], row["source_system"]) in active for row in self.data["records"]))
        self.assertTrue(any(not row["active"] for row in self.data["opportunities"]))

    def test_audit_design_and_nonuniform_probabilities(self):
        frames = {row["frame_id"] for row in self.data["audit_frames"]}
        self.assertTrue(all(row["frame_id"] in frames and 0 < row["selection_probability"] <= 1 for row in self.data["audits"]))
        self.assertGreater(len({row["selection_probability"] for row in self.data["audits"]}), 1)
        self.assertTrue(any(not row["found_in_registry"] for row in self.data["audits"]))
        self.assertTrue(self.data["followup_audits"])
