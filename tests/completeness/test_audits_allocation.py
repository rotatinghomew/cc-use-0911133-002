"""核查抽样框、不等概率、名额约束与反馈再入分析。"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from registry_delta_hub.completeness.audits import (
    build_audit_estimates,
    design_based_total_interval,
    implied_total_range,
)
from registry_delta_hub.completeness.data import prepare
from registry_delta_hub.completeness.pipeline import analyse
from registry_delta_hub.runtime import DomainError


ROOT = Path(__file__).resolve().parents[2]


def load():
    return json.loads((ROOT / "fixtures/completeness.json").read_text(encoding="utf-8"))


def frame(fid, region, window, eligible, version=1):
    return {
        "frame_id": fid,
        "region": region,
        "window": window,
        "eligible_units": eligible,
        "sampling_unit": "service-recipient",
        "version": version,
    }


def audit(aid, fid, p, received=True, found=True):
    return {
        "audit_id": aid,
        "frame_id": fid,
        "selection_probability": p,
        "received_service": received,
        "found_in_registry": found,
        "review_revision": 1,
    }


def record(observation_id, subject, source, region="r", window="w", revision=1, status="active"):
    return {
        "observation_id": observation_id,
        "subject_id": subject,
        "source_system": source,
        "region": region,
        "window": window,
        "revision": revision,
        "status": status,
    }


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.result = analyse(load())
        self.payload = self.result.payload

    def test_effective_sample_size_accounts_for_weights(self):
        estimate = self.result.audits["frame-town-spring"]
        self.assertLess(estimate.effective_n, estimate.n_design)
        self.assertGreater(estimate.design_effect, 1.0)

    def test_nonprobability_followup_is_excluded(self):
        # The fixture's follow-up row has a probability, so the exclusion rule
        # is exercised directly here.
        data = load()
        data["followup_audits"] = [
            {
                "audit_id": "targeted",
                "frame_id": "frame-town-spring",
                "received_service": True,
                "found_in_registry": False,
                "review_revision": 1,
            }
        ]
        prepared = prepare(data)
        updated = build_audit_estimates(prepared, include_followup=True)
        estimate = updated["frame-town-spring"]
        self.assertEqual(len(estimate.excluded_nonprob_rows), 1)
        self.assertIn("非概率", " ".join(estimate.notes))
        self.assertEqual(estimate.n_design, 8)

    def test_followup_does_not_enter_baseline(self):
        self.assertEqual(len(self.result.audits["frame-mountain-spring"].design_rows), 8)
        self.assertEqual(
            len(self.result.audits["frame-mountain-spring"].design_rows)
            + len(prepared_followup(self.result)),
            9,
        )

    def test_implied_total_stays_inside_frame_and_registry(self):
        for fid, estimate in self.result.audits.items():
            registered = self.result.data.registered_by_stratum[(estimate.region, estimate.window)]
            interval = implied_total_range(registered, estimate, estimate.eligible_units)
            self.assertIsNotNone(interval)
            self.assertGreaterEqual(interval[0], registered)
            self.assertLessEqual(interval[1], estimate.eligible_units)

    def test_design_based_interval_respects_frame_ceiling(self):
        for estimate in self.result.audits.values():
            interval = design_based_total_interval(estimate)
            self.assertIsNotNone(interval)
            self.assertLessEqual(interval[1], estimate.eligible_units)
            self.assertGreaterEqual(interval[0], 0.0)

    def test_superseded_frame_version_never_used(self):
        data = load()
        # Add an older duplicate frame version with different eligible units.
        data["audit_frames"].append(
            frame("frame-town-spring-v0", "town", "spring", 999, version=1)
        )
        prepared = prepare(data)
        self.assertEqual(prepared.current_frames[("town", "spring")]["version"], 1)
        # A second frame at the same version is not preferred; the tie is
        # reported rather than silently merged.
        self.assertTrue(prepared.superseded_frames)
        self.assertIn(
            "frame-town-spring-v0",
            {item["frame_id"] for item in prepared.superseded_frames},
        )

    def test_duplicate_audit_id_rejected(self):
        data = load()
        data["audits"].append(dict(data["audits"][0]))
        with self.assertRaises(DomainError):
            prepare(data)


def prepared_followup(result):
    return [row for row in result.data.followup_audits if row["design_eligible"]]


class AllocationTests(unittest.TestCase):
    def setUp(self):
        self.result = analyse(load())
        self.allocation = self.result.payload["allocation"]

    def test_obeys_budget_and_caps(self):
        self.assertEqual(self.allocation["summary"]["slots_used"], 12)
        slots = {row["stratum"]: row["recommended_slots"] for row in self.allocation["recommendations"]}
        self.assertEqual(sum(slots.values()), 12)
        for row in self.allocation["recommendations"]:
            self.assertLessEqual(row["recommended_slots"], row["frame_cap"])
            self.assertLessEqual(row["recommended_slots"], row["sampling_headroom"])

    def test_priority_is_uncertainty_reduction_not_highest_miss_rate(self):
        # mountain/spring has the highest observed miss rate and also gets the
        # most slots; the town strata get slots for upper-bound width even
        # though their observed miss rate is zero.  A simple "highest miss rate"
        # rule would give all slots to one stratum only.
        slots = {row["stratum"]: row["recommended_slots"] for row in self.allocation["recommendations"]}
        self.assertEqual(slots["mountain/spring"], 5)
        self.assertGreater(slots["town/spring"], 0)
        self.assertGreater(slots["town/summer"], 0)

    def test_mismatched_frame_receives_no_slots_with_reason(self):
        row = next(
            item for item in self.allocation["recommendations"]
            if item["stratum"] == "mountain/summer"
        )
        self.assertEqual(row["recommended_slots"], 0)
        self.assertIn("框范围", row["why_zero_slots"])
        constraint = next(
            item for item in self.allocation["constraints"]
            if item["stratum"] == "mountain/summer"
        )
        self.assertEqual(constraint["constraint"], "frame-scope-mismatch")

    def test_baseline_matches_published_interval(self):
        for row in self.allocation["recommendations"]:
            stratum = next(
                item for item in self.result.payload["strata"]
                if item["key"] == row["stratum"]
            )
            self.assertEqual(
                row["baseline_total_interval"],
                stratum["recommendation"]["total_interval"],
            )

    def test_each_step_names_the_uncertainty_it_narrows(self):
        for step in self.allocation["summary"]["incremental_steps"]:
            self.assertTrue(step["why"])
            self.assertGreater(step["expected_width_reduction"], 0)


class FollowupAttributionTests(unittest.TestCase):
    def setUp(self):
        self.impact = analyse(load()).payload["followup_impact"]

    def test_followup_changes_mountain_spring_only(self):
        changes = {row["stratum"]: row for row in self.impact["stratum_changes"]}
        self.assertTrue(changes["mountain/spring"]["changed"])
        self.assertIn("核查证据变化", " ".join(changes["mountain/spring"]["attribution"]))
        self.assertFalse(changes["town/spring"]["changed"])

    def test_attribution_separates_frame_version_from_evidence(self):
        self.assertIn("frame_version_used", analyse(load()).payload["scenario_sensitivity"][0])
        for row in self.impact["stratum_changes"]:
            self.assertTrue(row["attribution"])

    def test_new_frame_version_without_audits_is_attributed_to_frame_change(self):
        data = load()
        data["audit_frames"].append(
            frame("frame-town-spring-v2", "town", "spring", 80, version=2)
        )
        payload = analyse(data).payload
        row = next(
            item for item in payload["scenario_sensitivity"]
            if item["stratum"] == "town/spring"
        )
        self.assertEqual(row["domain"], "frame-version-changed")
        self.assertEqual(row["audit_rows_on_superseded_frames"], 8)
        self.assertTrue(any("已被取代" in warning for warning in payload["warnings"]))


if __name__ == "__main__":
    unittest.main()
