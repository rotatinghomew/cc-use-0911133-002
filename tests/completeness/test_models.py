"""模型比较：来源依赖、人群异质性、独立核查检验与不可识别结论。"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from registry_delta_hub.completeness.audits import build_audit_estimates
from registry_delta_hub.completeness.data import prepare
from registry_delta_hub.completeness.models import (
    AUDIT,
    BOUNDS,
    COPY_AWARE,
    COVERAGE,
    NAIVE,
    analyse_stratum,
)
from registry_delta_hub.completeness.validation import (
    copy_dependency_case,
    independent_sources_case,
    unequal_probability_audit_case,
    whole_source_outage_case,
)

ROOT = Path(__file__).resolve().parents[2]


def load():
    return json.loads((ROOT / "fixtures/completeness.json").read_text(encoding="utf-8"))


def analyses():
    data = prepare(load())
    audits = build_audit_estimates(data)
    return {
        f"{region}/{window}": analyse_stratum(
            data, region, window, audits[data.current_frames[(region, window)]["frame_id"]]
        )
        for region, window in data.strata
    }


def candidate(stratum, name):
    return next(item for item in stratum.candidates if item.name == name)


class SourceDependenceTests(unittest.TestCase):
    def setUp(self):
        self.strata = analyses()

    def test_partner_copy_in_town_is_detected(self):
        town = self.strata["town/spring"]
        self.assertEqual(town.dependence_level, "suspected-copy")
        text = " ".join(town.dependence_evidence)
        self.assertIn("partner", text)
        self.assertIn("机械包含", text)

    def test_naive_model_is_degenerate_when_sources_are_nested(self):
        naive = candidate(self.strata["town/summer"], NAIVE)
        self.assertTrue(naive.identified)
        self.assertTrue(naive.degenerate)
        self.assertEqual(naive.point, 24.0)
        self.assertEqual(naive.interval, (24.0, 24.0))

    def test_copy_aware_model_differs_from_naive(self):
        naive = candidate(self.strata["town/spring"], NAIVE)
        aware = candidate(self.strata["town/spring"], COPY_AWARE)
        self.assertNotEqual(naive.point, aware.point)
        self.assertGreater(aware.point, naive.point)

    def test_mountain_has_no_copy_evidence_in_window(self):
        # partner is declared as a possible copy target but has no records, so
        # it must not create dependence evidence for the mountain strata.
        mountain = self.strata["mountain/spring"]
        self.assertEqual(mountain.dependence_level, "none")
        self.assertTrue(
            any("没有记录" in item for item in mountain.dependence_evidence)
        )

    def test_nested_sources_are_not_used_as_independent_recapture(self):
        aware = candidate(self.strata["town/spring"], COPY_AWARE)
        self.assertIsNotNone(aware.point)
        self.assertNotEqual(aware.point, 24.0)


class AuditCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.strata = analyses()

    def test_audit_points_out_naive_understatement_in_mountain_spring(self):
        tests = {test["candidate"]: test for test in self.strata["mountain/spring"].audit_tests}
        self.assertEqual(tests[NAIVE]["verdict"], "model-understates-miss")
        self.assertGreater(tests[NAIVE]["implied_miss_rate"], 0)

    def test_audit_rejects_copy_aware_overstatement_in_town(self):
        tests = {test["candidate"]: test for test in self.strata["town/spring"].audit_tests}
        self.assertEqual(tests[COPY_AWARE]["verdict"], "model-overstates-miss")

    def test_audit_is_the_recommended_basis_where_lists_are_dependent(self):
        town = self.strata["town/spring"]
        self.assertEqual(town.recommendation["basis"], AUDIT)
        self.assertEqual(town.recommendation["estimability"], "audit-bounded")
        self.assertIsNotNone(town.recommendation["total_interval"])

    def test_mismatched_frame_is_assumption_sensitive_not_a_false_precise_total(self):
        summer = self.strata["mountain/summer"]
        self.assertEqual(summer.recommendation["estimability"], "assumption-sensitive")
        statuses = {check["check"]: check["status"] for check in summer.cross_frame_checks}
        self.assertEqual(
            statuses["audit-found-vs-window-registry"], "frame-registry-population-mismatch"
        )
        # The union interval must span all three interpretations and the frame.
        low, high = summer.recommendation["total_interval"]
        self.assertLessEqual(low, 12.0)
        self.assertGreaterEqual(high, 50.0)

    def test_design_free_bounds_never_claim_more_precision(self):
        for stratum in self.strata.values():
            bounds = candidate(stratum, BOUNDS)
            self.assertTrue(bounds.identified)
            self.assertIsNone(bounds.point)
            self.assertEqual(bounds.interval[0], float(stratum.registered))

    def test_coverage_extrapolation_assumes_homogeneity(self):
        for stratum in self.strata.values():
            coverage = candidate(stratum, COVERAGE)
            self.assertIn("同质", coverage.assumption)
            self.assertIsNone(coverage.interval)


class IdentifiabilityEdgeTests(unittest.TestCase):
    def test_confirming_copy_keeps_refusal_even_with_high_overlap(self):
        data = load()
        for relationship in data["source_relationships"]:
            relationship["confirmed"] = True
        prepared = prepare(data)
        audits = build_audit_estimates(prepared)
        town = analyse_stratum(
            prepared, "town", "spring",
            audits[prepared.current_frames[("town", "spring")]["frame_id"]],
        )
        self.assertEqual(town.dependence_level, "confirmed-copy")
        # Lists alone must not identify the population, but the independent
        # audit still bounds the total.
        self.assertEqual(town.recommendation["basis"], AUDIT)
        self.assertIn("抄送", " ".join(town.recommendation["reasons"]))

    def test_no_audit_means_no_point_total(self):
        data = load()
        data["audits"] = []
        data["followup_audits"] = []
        data["audit_frames"] = []
        data["frame_caps"] = {}
        data["audit_budget"] = 0
        prepared = prepare(data)
        for region, window in prepared.strata:
            stratum = analyse_stratum(prepared, region, window, None)
            self.assertIsNone(stratum.recommendation["total_interval"])
            self.assertEqual(stratum.recommendation["estimability"], "not-identifiable")
            self.assertIsNone(stratum.recommendation["possible_missed_interval"])

    def test_single_source_stratum_is_not_identifiable(self):
        data = load()
        for opportunity in data["opportunities"]:
            if (opportunity["region"], opportunity["window"]) == ("mountain", "spring") \
                    and opportunity["source"] == "hotline":
                opportunity["active"] = False
        prepared = prepare(data)
        audits = build_audit_estimates(prepared)
        stratum = analyse_stratum(
            prepared, "mountain", "spring",
            audits[prepared.current_frames[("mountain", "spring")]["frame_id"]],
        )
        naive = candidate(stratum, NAIVE)
        self.assertFalse(naive.identified)
        self.assertIn("没有", " ".join(naive.notes))


class ValidationScenarioTests(unittest.TestCase):
    def test_independent_sources_recover_truth(self):
        case = independent_sources_case()
        self.assertTrue(case["passes"])
        self.assertGreater(case["naive_estimate"], case["registered"])

    def test_whole_source_outage_does_not_create_recapture(self):
        case = whole_source_outage_case()
        self.assertTrue(case["passes"])
        self.assertEqual(case["closed_sources"], ["c"])
        self.assertEqual(case["active_sources"], ["a", "b"])

    def test_copy_dependency_refuses_list_total(self):
        case = copy_dependency_case()
        self.assertTrue(case["passes"])
        self.assertTrue(case["naive_degenerate"])

    def test_unequal_probability_weighting(self):
        case = unequal_probability_audit_case()
        self.assertTrue(case["passes"])
        self.assertGreater(case["design_based_miss_rate"], case["unweighted_miss_rate"])
        self.assertGreater(case["effective_n"], 0)


if __name__ == "__main__":
    unittest.main()
