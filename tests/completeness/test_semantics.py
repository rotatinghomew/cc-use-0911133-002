"""登记语义：revision、撤回、停运来源、跨窗口总体。"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from registry_delta_hub.completeness.data import prepare
from registry_delta_hub.runtime import DomainError

ROOT = Path(__file__).resolve().parents[2]


def load():
    return json.loads((ROOT / "fixtures/completeness.json").read_text(encoding="utf-8"))


def record(observation_id, subject, source, region, window, revision=1, status="active"):
    return {
        "observation_id": observation_id,
        "subject_id": subject,
        "source_system": source,
        "region": region,
        "window": window,
        "revision": revision,
        "status": status,
    }


def minimal(records, opportunities, sources=("a", "b")):
    return {
        "schema_version": 1,
        "sources": list(sources),
        "records": records,
        "opportunities": opportunities,
        "source_relationships": [],
        "audit_frames": [],
        "audits": [],
        "followup_audits": [],
        "audit_budget": 0,
        "frame_caps": {},
    }


def opp(sources, region, window, closed=()):
    return [
        {"region": region, "window": window, "source": s, "active": s not in closed}
        for s in sources
    ]


class RevisionTests(unittest.TestCase):
    def test_fixture_dedupes_rows_to_residents(self):
        data = prepare(load())
        self.assertEqual(len(data.records.rows), 153)
        self.assertEqual(data.records.superseded_rows, 1)
        self.assertEqual(len(data.records.withdrawn), 1)
        self.assertEqual(data.registered_total, 71)
        self.assertEqual(len(data.records.active), 151)

    def test_withdrawn_latest_revision_removes_observation(self):
        data = prepare(load())
        self.assertEqual(
            data.records.latest["obs-0-hotline"]["status"], "withdrawn"
        )
        self.assertEqual(data.records.latest["obs-0-hotline"]["revision"], 2)
        self.assertFalse(
            any(row["subject_id"] == "subject-000" for row in data.records.active)
        )

    def test_lower_revision_after_higher_is_ignored(self):
        # Order must not matter: highest revision always wins.
        rows = [
            record("o", "s", "a", "r", "w", revision=2, status="withdrawn"),
            record("o", "s", "a", "r", "w", revision=1, status="active"),
        ]
        data = prepare(minimal(rows, opp(("a", "b"), "r", "w")))
        self.assertEqual(len(data.records.active), 0)
        self.assertEqual(len(data.records.withdrawn), 1)

    def test_same_revision_conflict_is_reported_not_silently_merged(self):
        rows = [
            record("o", "s", "a", "r", "w", revision=1, status="active"),
            record("o", "s", "a", "r", "w", revision=1, status="withdrawn"),
        ]
        data = prepare(minimal(rows, opp(("a", "b"), "r", "w")))
        self.assertEqual(len(data.records.conflicting_revisions), 1)
        self.assertTrue(any("冲突" in warning for warning in data.warnings))


class OpportunityTests(unittest.TestCase):
    def test_closed_source_is_flagged_not_counted_as_miss(self):
        data = prepare(load())
        self.assertEqual(
            data.inactive_sources[("mountain", "spring")], ["mobile"]
        )
        self.assertEqual(
            data.active_sources[("town", "summer")],
            ["hotline", "community", "mobile", "partner"],
        )

    def test_record_from_closed_source_is_excluded_and_warned(self):
        rows = [
            record("a-1", "s1", "a", "r", "w"),
            record("c-2", "s2", "c", "r", "w"),
        ]
        data = prepare(
            minimal(rows, opp(("a", "b", "c"), "r", "w", closed=("c",)),
                    sources=("a", "b", "c"))
        )
        self.assertEqual(len(data.records.excluded_inactive_source), 1)
        self.assertEqual(data.registered_total, 1)
        self.assertTrue(any("已排除" in w for w in data.warnings))

    def test_source_working_but_empty_is_visible(self):
        rows = [record("a-1", "s1", "a", "r", "w")]
        data = prepare(minimal(rows, opp(("a", "b"), "r", "w")))
        self.assertEqual(data.registered_total, 1)
        pattern = data.capture_patterns[("r", "w")]
        self.assertEqual(pattern, {("a",): 1})


class PopulationScopeTests(unittest.TestCase):
    def test_windows_are_stratified_not_one_closed_population(self):
        data = prepare(load())
        self.assertEqual(
            data.strata,
            [("mountain", "spring"), ("mountain", "summer"),
             ("town", "spring"), ("town", "summer")],
        )
        self.assertEqual(data.cross_window_subjects, {})

    def test_cross_window_resident_counted_once_and_listed(self):
        rows = [
            record("a-1", "s1", "a", "r", "w1"),
            record("b-1", "s2", "b", "r", "w1"),
            record("a-2", "s1", "a", "r", "w2"),
            record("b-3", "s3", "b", "r", "w2"),
        ]
        data = prepare(
            minimal(rows, opp(("a", "b"), "r", "w1") + opp(("a", "b"), "r", "w2"))
        )
        self.assertEqual(data.registered_total, 3)
        self.assertEqual(data.cross_window_subjects, {"s1": [("r", "w1"), ("r", "w2")]})

    def test_invalid_revision_and_status_raise(self):
        with self.assertRaises(DomainError):
            prepare(minimal([record("o", "s", "a", "r", "w", revision=0)],
                            opp(("a", "b"), "r", "w")))
        with self.assertRaises(DomainError):
            prepare(minimal([record("o", "s", "a", "r", "w", status="opened")],
                            opp(("a", "b"), "r", "w")))


if __name__ == "__main__":
    unittest.main()
