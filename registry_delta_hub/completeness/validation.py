"""Self-checks that exercise the analysis chain on small, interpretable cases.

Four named scenarios are always run and their outcomes are embedded in the
report, so a reader can see *why* the pipeline's claims hold rather than
trusting the fixture run alone:

``independent-sources``
    Two genuinely independent sources; recapture should recover the truth.

``whole-source-outage``
    A source that did not operate must not look like missed residents, and must
    not be used as a recapture.

``copy-dependency``
    A source that copies another produces high overlap; list-only totals must
    refuse to identify the population.

``unequal-probability-audit``
    An audit with unequal inclusion probabilities must be weighted and must not
    be treated as a simple random sample.
"""
from __future__ import annotations

from typing import Any

from registry_delta_hub.completeness.audits import build_audit_estimates, implied_total_range
from registry_delta_hub.completeness.data import prepare
from registry_delta_hub.completeness.models import analyse_stratum


def _fixture(
    sources: list[str],
    records: list[dict[str, Any]],
    opportunities: list[dict[str, Any]],
    frames: list[dict[str, Any]] | None = None,
    audits: list[dict[str, Any]] | None = None,
    relationships: list[dict[str, Any]] | None = None,
    budget: int = 0,
    caps: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "sources": sources,
        "records": records,
        "opportunities": opportunities,
        "source_relationships": relationships or [],
        "audit_frames": frames or [],
        "audits": audits or [],
        "followup_audits": [],
        "audit_budget": budget,
        "frame_caps": caps or {},
    }


def _record(observation_id: str, subject: str, source: str, region: str, window: str) -> dict[str, Any]:
    return {
        "observation_id": observation_id,
        "subject_id": subject,
        "source_system": source,
        "region": region,
        "window": window,
        "revision": 1,
        "status": "active",
    }


def _opportunities(sources: list[str], region: str, window: str, closed: list[str] | None = None) -> list[dict[str, Any]]:
    closed = closed or []
    return [
        {"region": region, "window": window, "source": source, "active": source not in closed}
        for source in sources
    ]


def _frame(frame_id: str, region: str, window: str, eligible: int, version: int = 1) -> dict[str, Any]:
    return {
        "frame_id": frame_id,
        "region": region,
        "window": window,
        "eligible_units": eligible,
        "sampling_unit": "service-recipient",
        "version": version,
    }


def _audit(audit_id: str, frame_id: str, probability: float, received: bool, found: bool) -> dict[str, Any]:
    return {
        "audit_id": audit_id,
        "frame_id": frame_id,
        "selection_probability": probability,
        "received_service": received,
        "found_in_registry": found,
        "review_revision": 1,
    }


def _stratify(data: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    prepared = prepare(data)
    audits = build_audit_estimates(prepared)
    results = []
    for region, window in prepared.strata:
        frame = prepared.current_frames.get((region, window))
        audit = audits.get(frame["frame_id"]) if frame else None
        results.append(
            analyse_stratum(prepared, region, window, audit).to_dict()
        )
    return [f"{region}/{window}" for region, window in prepared.strata], results


def independent_sources_case() -> dict[str, Any]:
    # True served population 20 per source with independent capture p=0.5:
    # 10 in each, 5 in both, 15 distinct observed.  Chapman should recover ~19.
    sources = ["a", "b"]
    records = []
    for index in range(10):
        records.append(_record(f"a-{index}", f"s-{index}", "a", "r", "w"))
    for index in range(5, 15):
        records.append(_record(f"b-{index}", f"s-{index}", "b", "r", "w"))
    data = _fixture(sources, records, _opportunities(sources, "r", "w"))
    strata, results = _stratify(data)
    result = results[0]
    naive = next(item for item in result["candidates"] if item["name"] == "naive-independence")
    return {
        "scenario": "independent-sources",
        "registered": result["registered_residents"],
        "expected_direction": "再捕获估计应高于合并人数并接近真实值",
        "naive_estimate": naive["point"],
        "naive_identified": naive["identified"],
        "passes": bool(naive["identified"] and naive["point"] and 15 < naive["point"] < 25),
        "detail": "两个独立来源，真实总体 20 人，合并后仅见 15 人",
    }


def whole_source_outage_case() -> dict[str, Any]:
    # Source c is closed in window w2 and must not create a recapture or a miss.
    sources = ["a", "b", "c"]
    records = [
        _record("a-0", "s-0", "a", "r", "w1"),
        _record("a-1", "s-1", "a", "r", "w1"),
        _record("b-0", "s-0", "b", "r", "w1"),
        _record("a-2", "s-2", "a", "r", "w2"),
        _record("b-3", "s-3", "b", "r", "w2"),
    ]
    data = _fixture(
        sources,
        records,
        _opportunities(sources, "r", "w1") + _opportunities(sources, "r", "w2", closed=["c"]),
    )
    _, results = _stratify(data)
    w2 = next(item for item in results if item["key"] == "r/w2")
    return {
        "scenario": "whole-source-outage",
        "closed_sources": w2["inactive_sources"],
        "active_sources": w2["active_sources"],
        "registered": w2["registered_residents"],
        "passes": w2["inactive_sources"] == ["c"] and "c" not in w2["active_sources"],
        "detail": "来源 c 在 w2 未工作；其缺位不是居民漏登",
    }


def copy_dependency_case() -> dict[str, Any]:
    # b copies a: same 10 residents.  c sees 4 of them independently.
    sources = ["a", "b", "c"]
    records = []
    for index in range(10):
        records.append(_record(f"a-{index}", f"s-{index}", "a", "r", "w"))
        records.append(_record(f"b-{index}", f"s-{index}", "b", "r", "w"))
    for index in range(0, 4):
        records.append(_record(f"c-{index}", f"s-{index}", "c", "r", "w"))
    data = _fixture(
        sources,
        records,
        _opportunities(sources, "r", "w"),
        relationships=[{"from": "a", "to": "b", "kind": "possible-copy", "confirmed": False}],
    )
    _, results = _stratify(data)
    result = results[0]
    naive = next(item for item in result["candidates"] if item["name"] == "naive-independence")
    copy_aware = next(item for item in result["candidates"] if item["name"] == "copy-aware-independence")
    return {
        "scenario": "copy-dependency",
        "registered": result["registered_residents"],
        "dependence_level": result["dependence"]["level"],
        "naive_degenerate": naive["degenerate_zero_width"],
        "copy_aware_point": copy_aware["point"],
        "passes": bool(
            result["dependence"]["level"] in {"suspected-copy", "confirmed-copy"}
            and naive["degenerate_zero_width"]
            and result["recommendation"]["estimability"] in {"not-identifiable", "bounds-only", "assumption-sensitive"}
        ),
        "detail": "a 与 b 是同一名册；合并人数不是再捕获总数",
    }


def unequal_probability_audit_case() -> dict[str, Any]:
    # Frame of 100.  Two design strata: p=0.5 (found) and p=0.1 (mostly missed),
    # so the unweighted miss rate is not the design-based one.
    sources = ["a", "b"]
    records = [
        _record(f"a-{index}", f"s-{index}", "a", "r", "w")
        for index in range(10)
    ]
    opportunities = _opportunities(sources, "r", "w")
    frame = _frame("f", "r", "w", 100)
    audits = []
    # Four p=0.5 draws, all found; four p=0.1 draws, all missed.
    for index in range(4):
        audits.append(_audit(f"hi-{index}", "f", 0.5, True, True))
    for index in range(4):
        audits.append(_audit(f"lo-{index}", "f", 0.1, True, False))
    data = _fixture(sources, records, opportunities, [frame], audits)
    prepared = prepare(data)
    estimates = build_audit_estimates(prepared)
    estimate = estimates["f"]
    unweighted = 4 / 8
    weighted = estimate.missed_weight / estimate.received_weight
    interval = implied_total_range(10, estimate, 100.0)
    return {
        "scenario": "unequal-probability-audit",
        "unweighted_miss_rate": round(unweighted, 3),
        "design_based_miss_rate": round(weighted, 3),
        "effective_n": round(estimate.effective_n, 3),
        "design_effect": None if estimate.design_effect is None else round(estimate.design_effect, 3),
        "implied_total_interval": None if interval is None else [round(interval[0], 2), round(interval[1], 2)],
        "passes": bool(weighted > unweighted and interval is not None),
        "detail": "低入样概率的漏登单位权重高十倍；简单平均会低估漏登",
    }


def run_validations() -> list[dict[str, Any]]:
    return [
        independent_sources_case(),
        whole_source_outage_case(),
        copy_dependency_case(),
        unequal_probability_audit_case(),
    ]
