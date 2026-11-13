"""Orchestration: prepared data -> strata analyses -> report payload.

The entry point is :func:`analyse`; it is pure (no database, no files written)
so tests can exercise the whole chain on small synthetic fixtures.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from registry_delta_hub.completeness.allocation import (
    allocate,
    allocation_constraints,
)
from registry_delta_hub.completeness.audits import (
    AuditEstimate,
    build_audit_estimates,
    followup_comparison,
)
from registry_delta_hub.completeness.data import PreparedData, prepare
from registry_delta_hub.completeness.models import (
    StratumAnalysis,
    aggregate_candidates,
    analyse_stratum,
)
from registry_delta_hub.completeness.validation import run_validations


@dataclass(slots=True)
class RunResult:
    data: PreparedData
    strata: list[StratumAnalysis]
    audits: dict[str, AuditEstimate]
    payload: dict[str, Any]


def _stratum_audit(
    data: PreparedData, region: str, window: str, audits: dict[str, AuditEstimate]
) -> AuditEstimate | None:
    frame = data.current_frames.get((region, window))
    if frame is None:
        return None
    return audits.get(frame["frame_id"])


def _run_strata(
    data: PreparedData, audits: dict[str, AuditEstimate]
) -> list[StratumAnalysis]:
    return [
        analyse_stratum(data, region, window, _stratum_audit(data, region, window, audits))
        for region, window in data.strata
    ]


def _registered_summary(data: PreparedData) -> dict[str, Any]:
    raw_rows = len(data.records.rows)
    resolved = len(data.records.latest)
    active = len(data.records.active)
    excluded = len(data.records.excluded_inactive_source)
    return {
        "raw_rows": raw_rows,
        "resolved_observations": resolved,
        "superseded_rows": data.records.superseded_rows,
        "withdrawn_observations": len(data.records.withdrawn),
        "active_observations": active,
        "active_from_inactive_sources_excluded": excluded,
        "distinct_registered_residents": data.registered_total,
        "merged_row_count_is_not_a_total": True,
        "row_to_resident_note": (
            f"{raw_rows} 行合并记录解析为 {data.registered_total} 名不同居民；"
            "行数是来源出现次数，不是人数"
        ),
        "by_stratum": {
            f"{region}/{window}": data.registered_by_stratum[(region, window)]
            for region, window in data.strata
        },
        "cross_window_residents": {
            "count": len(data.cross_window_subjects),
            "subjects": sorted(data.cross_window_subjects),
            "note": (
                "同一居民在多个服务窗口出现时不重复累加；窗口是不同服务暴露机会，"
                "不合并为一个封闭总体"
            ),
        },
    }


def _opportunity_matrix(data: PreparedData) -> list[dict[str, Any]]:
    rows = []
    for region, window in data.strata:
        rows.append(
            {
                "region": region,
                "window": window,
                "working_sources": list(data.active_sources[(region, window)]),
                "closed_or_not_operating_sources": list(data.inactive_sources[(region, window)]),
                "note": "closed source means no capture opportunity, so absence is not evidence of a missed resident",
            }
        )
    return rows


def _stratum_totals(strata: list[StratumAnalysis]) -> dict[str, Any]:
    recommended = [stratum for stratum in strata if stratum.recommendation["total_interval"]]
    lows = [stratum.recommendation["total_interval"][0] for stratum in recommended]
    highs = [stratum.recommendation["total_interval"][1] for stratum in recommended]
    missed_lows = [
        stratum.recommendation["possible_missed_interval"][0]
        for stratum in recommended
        if stratum.recommendation["possible_missed_interval"]
    ]
    missed_highs = [
        stratum.recommendation["possible_missed_interval"][1]
        for stratum in recommended
        if stratum.recommendation["possible_missed_interval"]
    ]
    return {
        "strata_with_recommendation": len(recommended),
        "strata_total": len(strata),
        "sum_of_recommended_total_intervals": [round(sum(lows), 2), round(sum(highs), 2)]
        if recommended
        else None,
        "sum_of_possible_missed_intervals": [round(sum(missed_lows), 2), round(sum(missed_highs), 2)]
        if missed_lows
        else None,
        "registered_residents": sum(stratum.registered for stratum in strata),
        "caveat": (
            "逐层区间求和只是算术和，不是联合置信区间；"
            "各层是不同服务窗口，没有被当作同一封闭总体。"
        ),
    }


def _estimability_breakdown(strata: list[StratumAnalysis]) -> dict[str, Any]:
    buckets: dict[str, list[str]] = {}
    for stratum in strata:
        label = stratum.recommendation["estimability"]
        buckets.setdefault(label, []).append(stratum.key)
    explanations = {
        "audit-bounded": "independent probability audit bounds the total without an independence assumption",
        "model-and-audit-consistent": "a list-based model and the audit agree on the missed share",
        "assumption-sensitive": "the audit frame and the registry window population may differ; report a range of assumptions",
        "bounds-only": "no usable probability audit; only the design-free bounds remain",
        "not-identifiable": "no recapture contrast or no audit; no defensible total",
        "not-identifiable-from-lists": "list-only models are invalid because sources copy or nest",
    }
    return {
        "buckets": [
            {
                "estimability": label,
                "strata": strata_keys,
                "explanation": explanations.get(label, ""),
            }
            for label, strata_keys in sorted(buckets.items())
        ],
        "note": "可估性是数据设计的属性，不是估计值大小的属性",
    }


def _followup_impact(
    data: PreparedData,
    baseline: dict[str, AuditEstimate],
    updated: dict[str, AuditEstimate],
) -> dict[str, Any]:
    comparisons = followup_comparison(baseline, updated)
    strata_before = {stratum.key: stratum for stratum in _run_strata(data, baseline)}
    strata_after = _run_strata(data, updated)
    changes = []
    for stratum in strata_after:
        before = strata_before[stratum.key]
        if before.recommendation["total_interval"] == stratum.recommendation["total_interval"]:
            changed = False
        else:
            changed = True
        changes.append(
            {
                "stratum": stratum.key,
                "before": {
                    "basis": before.recommendation["basis"],
                    "total_interval": before.recommendation["total_interval"],
                    "estimability": before.recommendation["estimability"],
                },
                "after": {
                    "basis": stratum.recommendation["basis"],
                    "total_interval": stratum.recommendation["total_interval"],
                    "estimability": stratum.recommendation["estimability"],
                },
                "changed": changed,
                "attribution": _attribute_change(before, stratum),
            }
        )
    return {
        "baseline_audit_ids": sorted(row["audit_id"] for row in data.audits),
        "followup_audit_ids": sorted(row["audit_id"] for row in data.followup_audits),
        "followup_is_design_based": all(row["design_eligible"] for row in data.followup_audits)
        if data.followup_audits
        else None,
        "audit_comparison": comparisons,
        "stratum_changes": changes,
        "note": (
            "把新核查并入概率样本只改变核查证据；除非归因中明确写出，抽样框版本与模型定义都没有改动"
        ),
    }


def _attribute_change(before: StratumAnalysis, after: StratumAnalysis) -> list[str]:
    reasons = []
    if before.audit is None or after.audit is None:
        return ["一侧没有核查对象，无法归因"]
    if after.audit.n_design != before.audit.n_design:
        reasons.append(
            f"核查证据变化：新增 {after.audit.n_design - before.audit.n_design} 条设计型记录"
            f"（{before.audit.n_design} → {after.audit.n_design}）"
        )
    if before.audit.frame_id != after.audit.frame_id or before.audit.frame_version != after.audit.frame_version:
        reasons.append("抽样框版本变化，不是模型变化")
    if before.recommendation["basis"] != after.recommendation["basis"]:
        reasons.append(
            f"证据变化导致模型选择变化：{before.recommendation['basis']} → "
            f"{after.recommendation['basis']}"
        )
    if set(before.dependence_evidence) != set(after.dependence_evidence):
        reasons.append("来源依赖证据变化，可能改变有效模型族")
    if not reasons:
        reasons.append("证据与模型都没有变化；设计记录与权重相同")
    return reasons


def _scenario_sensitivity(
    data: PreparedData, baseline: dict[str, AuditEstimate], updated: dict[str, AuditEstimate]
) -> list[dict[str, Any]]:
    """Frame-version vs evidence vs model sensitivity for every stratum."""
    rows = []
    all_audit_rows = list(data.audits) + list(data.followup_audits)
    for region, window in data.strata:
        frame = data.current_frames.get((region, window))
        before = _stratum_audit(data, region, window, baseline)
        after = _stratum_audit(data, region, window, updated)
        stratum_frame_ids = {
            frame["frame_id"]
            for frame in data.frames_by_id.values()
            if (frame["region"], frame["window"]) == (region, window)
        }
        superseded_ids = {
            frame["frame_id"]
            for frame in data.superseded_frames
            if (frame["region"], frame["window"]) == (region, window)
        }
        stranded = [
            row for row in all_audit_rows if row["frame_id"] in superseded_ids
        ]
        current_rows = [
            row
            for row in all_audit_rows
            if frame is not None and row["frame_id"] == frame["frame_id"]
        ]
        if frame is None:
            rows.append(
                {
                    "stratum": f"{region}/{window}",
                    "frame_id": None,
                    "frame_version_used": None,
                    "frame_eligible_units": None,
                    "baseline_audit_rows": before.n_design if before else None,
                    "followup_audit_rows": after.n_design if after else None,
                    "audit_rows_on_superseded_frames": len(stranded),
                    "domain": "no-frame",
                    "interpretation": "没有核查抽样框；无法区分抽样框与模型变化，只能报告设计无关上下界",
                }
            )
            continue
        if stranded and not current_rows:
            domain = "frame-version-changed"
            interpretation = (
                "当前抽样框版本没有核查记录，全部核查仍挂在旧版本框上；"
                "结论从核查定界退为上下界属于框版本变化，不是模型变化。"
                "要恢复核查定界，需在新框上重抽或明确新旧框的对应关系"
            )
        elif stranded and current_rows:
            domain = "mixed-frame-versions"
            interpretation = (
                "新旧框上都有核查记录；估计只使用当前框，旧框记录不参与，"
                "因此任何变化兼有框版本与证据两种来源，不能只归因于其一"
            )
        else:
            domain = "same-frame-version"
            interpretation = "同一抽样框版本；任何变化来自核查证据，或来自在证据上重算的模型"
        rows.append(
            {
                "stratum": f"{region}/{window}",
                "frame_id": frame["frame_id"],
                "frame_version_used": frame["version"],
                "frame_eligible_units": frame["eligible_units"],
                "baseline_audit_rows": before.n_design if before else None,
                "followup_audit_rows": after.n_design if after else None,
                "audit_rows_on_superseded_frames": len(stranded),
                "domain": domain,
                "interpretation": interpretation,
            }
        )
    return rows


def analyse(data: dict[str, Any]) -> RunResult:
    prepared = prepare(data)
    audits = build_audit_estimates(prepared)
    strata = _run_strata(prepared, audits)
    updated_audits = build_audit_estimates(prepared, include_followup=True)
    slots, allocation_states, allocation_summary = allocate(
        strata, prepared.audit_budget, prepared.frame_caps
    )
    followup = _followup_impact(prepared, audits, updated_audits)
    stranded = [
        row["audit_id"]
        for row in prepared.audits
        if row["frame_id"] not in {frame["frame_id"] for frame in prepared.current_frames.values()}
    ]
    if stranded:
        prepared.warnings.append(
            "以下核查记录挂在已被取代的抽样框版本上，未进入设计估计："
            + "、".join(sorted(stranded))
        )
    payload: dict[str, Any] = {
        "schema_version": data.get("schema_version"),
        "seed": data.get("seed"),
        "scope": {
            "subject": "登记完整性证据与补录核查安排",
            "not": "服务资格、待遇或任何个人决定",
            "identity_matching": "未开发；subject_id 是已人工确认的统一居民键",
        },
        "registered": _registered_summary(prepared),
        "opportunities": {
            "matrix": _opportunity_matrix(prepared),
            "rule": "当期未工作的来源没有采集机会；它的缺位不是居民漏登",
        },
        "strata": [stratum.to_dict() for stratum in strata],
        "candidate_aggregates": aggregate_candidates(strata),
        "estimability": _estimability_breakdown(strata),
        "totals": _stratum_totals(strata),
        "audit_design": {
            "frames": [
                {
                    **{key: frame[key] for key in ("frame_id", "region", "window", "eligible_units", "version", "sampling_unit")},
                    "current": True,
                }
                for frame in sorted(prepared.current_frames.values(), key=lambda item: item["frame_id"])
            ],
            "superseded_frames": prepared.superseded_frames,
            "nonprobability_followup": [
                row for row in prepared.followup_audits if not row["design_eligible"]
            ],
            "excluded_nonprobability_total": {
                "baseline": sum(
                    1 for row in prepared.audits if not row["design_eligible"]
                ),
                "followup": sum(
                    1 for row in prepared.followup_audits if not row["design_eligible"]
                ),
                "note": "专门追查漏登的记录不在概率样本中，只能作为定性线索，不能当作随机入样",
            },
            "rule": "只有概率抽样核查进入设计估计；专门追查漏登的行动不是随机样本",
        },
        "allocation": {
            "budget": allocation_summary["budget"],
            "constraints": allocation_constraints(strata, prepared.audit_budget, prepared.frame_caps),
            "recommendations": slots,
            "summary": allocation_summary,
        },
        "followup_impact": followup,
        "scenario_sensitivity": _scenario_sensitivity(prepared, audits, updated_audits),
        "method_checks": {
            "scenarios": run_validations(),
            "note": "each scenario is a tiny synthetic fixture whose answer is known by construction",
        },
        "warnings": list(prepared.warnings),
    }
    return RunResult(data=prepared, strata=strata, audits=audits, payload=payload)


def analyse_followup_included(data: dict[str, Any]) -> RunResult:
    """Re-run the whole analysis with follow-up audits as primary evidence."""
    prepared = prepare(data)
    copied = dict(data)
    copied["audits"] = list(data.get("audits", [])) + list(data.get("followup_audits", []))
    copied["followup_audits"] = []
    prepared = prepare(copied)
    audits = build_audit_estimates(prepared)
    strata = _run_strata(prepared, audits)
    slots, _, allocation_summary = allocate(strata, prepared.audit_budget, prepared.frame_caps)
    payload = {
        "registered": _registered_summary(prepared),
        "strata": [stratum.to_dict() for stratum in strata],
        "audit_design": {
            "frames": [
                {
                    "frame_id": frame["frame_id"],
                    "eligible_units": frame["eligible_units"],
                    "version": frame["version"],
                }
                for frame in prepared.current_frames.values()
            ],
        },
        "allocation": {
            "recommendations": slots,
            "summary": allocation_summary,
        },
        "warnings": list(prepared.warnings),
    }
    return RunResult(data=prepared, strata=strata, audits=audits, payload=payload)
