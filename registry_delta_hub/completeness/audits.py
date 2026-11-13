"""Design-based audit calibration.

Audits are a probability sample from a frame *separate* from the registry, so
they are the only independent check on capture-recapture assumptions.  Three
rules matter:

* inclusion is governed by ``selection_probability``; every estimate is
  Horvitz-Thompson weighted.  A targeted hunt for missed people is *not* a
  probability sample and must not be pooled in or treated as random;
* uncertainty is expressed on an effective sample size (Kish design effect),
  because unequal inclusion probabilities reduce information;
* the audit measures coverage *inside its own frame*.  Translating that into a
  registry total requires an explicit assumption that the registry residents
  belong to the same served population, and that assumption is reported next
  to the number rather than hidden.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from registry_delta_hub.completeness.data import PreparedData
from registry_delta_hub.completeness.stats import (
    clopper_pearson,
    effective_sample_size,
    normal_quantile,
)


@dataclass(slots=True)
class AuditEstimate:
    frame_id: str
    region: str
    window: str
    frame_version: int
    eligible_units: int
    design_rows: list[dict[str, Any]]
    excluded_nonprob_rows: list[dict[str, Any]]
    n_design: int
    effective_n: float
    received_weight: float
    missed_weight: float
    miss_rate: float | None
    miss_interval: tuple[float, float] | None
    coverage_interval: tuple[float, float] | None
    design_effect: float | None
    status: str
    notes: list[str] = field(default_factory=list)


def audit_weights(rows: list[dict[str, Any]]) -> list[float]:
    return [1.0 / row["selection_probability"] for row in rows]


def build_audit_estimates(
    data: PreparedData, include_followup: bool = False
) -> dict[str, AuditEstimate]:
    """Estimate coverage per *current* frame from completed probability audits.

    ``followup_audits`` are excluded by default: they have not been merged into
    the first-round estimate.  ``include_followup=True`` is used only to
    produce the explicit re-run comparison.
    """
    estimates: dict[str, AuditEstimate] = {}
    for (region, window), frame in sorted(data.current_frames.items()):
        frame_id = frame["frame_id"]
        rows = [row for row in data.audits if row["frame_id"] == frame_id]
        if include_followup:
            rows = rows + [row for row in data.followup_audits if row["frame_id"] == frame_id]
        design = [row for row in rows if row["design_eligible"]]
        excluded = [row for row in rows if not row["design_eligible"]]
        notes: list[str] = []
        if excluded:
            notes.append(
                f"已把 {len(excluded)} 条非概率记录排除在设计估计之外："
                + "、".join(row["audit_id"] for row in excluded)
            )
        weights = audit_weights(design)
        effective = effective_sample_size(weights)
        received_weight = sum(
            weight for weight, row in zip(weights, design) if row["received_service"]
        )
        missed_weight = sum(
            weight
            for weight, row in zip(weights, design)
            if row["received_service"] and not row["found_in_registry"]
        )
        design_effect = len(design) / effective if design and effective > 0 else None
        miss_rate = None
        miss_interval = None
        coverage_interval = None
        status = "ok"
        if not design:
            status = "no-probability-sample"
            notes.append("没有可用的概率样本；本次核查无法校准本层")
        elif received_weight <= 0:
            status = "no-service-recipients"
            notes.append("样本中没有接受服务者；核查无法说明服务人群")
        else:
            miss_rate = missed_weight / received_weight
            lower, upper = clopper_pearson(miss_rate * effective, effective)
            miss_interval = (lower, upper)
            coverage_interval = (1.0 - upper, 1.0 - lower)
            if upper >= 1.0:
                status = "no-finite-coverage-lower-bound"
            elif miss_rate <= 0.0:
                status = "no-missed-in-sample"
        estimates[frame_id] = AuditEstimate(
            frame_id=frame_id,
            region=region,
            window=window,
            frame_version=frame["version"],
            eligible_units=frame["eligible_units"],
            design_rows=design,
            excluded_nonprob_rows=excluded,
            n_design=len(design),
            effective_n=effective,
            received_weight=received_weight,
            missed_weight=missed_weight,
            miss_rate=miss_rate,
            miss_interval=miss_interval,
            coverage_interval=coverage_interval,
            design_effect=design_effect,
            status=status,
            notes=notes,
        )
    return estimates


def implied_total_range(
    registered: int, estimate: AuditEstimate, upper_cap: float | None
) -> tuple[float, float] | None:
    """Translate an audit miss-rate interval into a registry-anchored total range.

    ``registered`` residents are known service recipients in this window.  If
    the audit frame covers the served population and the miss rate is ``p``,
    served ``= registered / (1 - p)``.  This is an assumption, not an identity;
    the caller reports it explicitly.
    """
    if estimate.miss_interval is None or registered <= 0:
        return None
    lower_rate, upper_rate = estimate.miss_interval
    cap = float(upper_cap) if upper_cap is not None else float(estimate.eligible_units)
    cap = max(cap, float(registered))
    lower_total = registered if lower_rate <= 0 else registered / (1.0 - lower_rate)
    if upper_rate >= 1.0:
        upper_total = cap
    else:
        upper_total = registered / (1.0 - upper_rate)
    lower_total = min(max(lower_total, float(registered)), cap)
    upper_total = min(max(upper_total, lower_total), cap)
    return lower_total, upper_total


def found_total_interval(estimate: AuditEstimate) -> tuple[float, float] | None:
    """设计型“在登记名册中找到的人数”区间。

    找到人数是核查与登记名册之间的直接对账量。若它显著大于该窗口的登记人数，
    说明核查框覆盖了更多“已登记”服务对象：要么是跨窗口重复，要么核查框与窗口
    登记人群不是同一总体。这里用 Poisson 抽样的 Horvitz-Thompson 方差检验。
    """
    if not estimate.design_rows:
        return None
    found_weight = 0.0
    variance = 0.0
    for row in estimate.design_rows:
        if not (row["received_service"] and row["found_in_registry"]):
            continue
        probability = row["selection_probability"]
        found_weight += 1.0 / probability
        variance += (1.0 - probability) / (probability * probability)
    if found_weight <= 0:
        return None
    half_width = 1.96 * variance ** 0.5
    return max(0.0, found_weight - half_width), min(float(estimate.eligible_units), found_weight + half_width)


def design_based_total_interval(
    estimate: AuditEstimate,
) -> tuple[float, float] | None:
    """Horvitz-Thompson served total with a normal-approximation interval.

    Treats the audit frame itself as the served population and does *not*
    anchor on the registry count.  Included so readers can see how much the
    answer changes when the frame, not the registry, defines the population.
    """
    if estimate.received_weight <= 0 or not estimate.design_rows:
        return None
    # Poisson-sampling Horvitz-Thompson variance for a domain total:
    #   Var = sum_i (1 - p_i) / p_i^2 * y_i
    # This respects unequal inclusion probabilities directly and stays inside
    # the frame, unlike a binomial share applied to the whole frame.
    variance = 0.0
    for row in estimate.design_rows:
        if not row["received_service"]:
            continue
        probability = row["selection_probability"]
        variance += (1.0 - probability) / (probability * probability)
    served = estimate.received_weight
    half_width = normal_quantile(0.975) * variance ** 0.5
    return (
        max(0.0, served - half_width),
        min(float(estimate.eligible_units), served + half_width),
    )


def model_audit_test(
    total: float | None, registered: int, estimate: AuditEstimate
) -> dict[str, Any] | None:
    """Compare a candidate total against the independent audit miss interval."""
    if total is None or estimate.miss_interval is None or total <= 0:
        return None
    implied_miss = max(0.0, (total - registered) / total)
    lower, upper = estimate.miss_interval
    if implied_miss < lower:
        verdict = "model-understates-miss"
    elif implied_miss > upper:
        verdict = "model-overstates-miss"
    else:
        verdict = "consistent-with-audit"
    return {
        "frame_id": estimate.frame_id,
        "total": round(total, 2),
        "registered": registered,
        "implied_miss_rate": round(implied_miss, 4),
        "audit_miss_interval": [round(lower, 4), round(upper, 4)],
        "verdict": verdict,
        "detail": (
            f"模型隐含漏登 {implied_miss:.1%}，核查区间 "
            f"{lower:.1%}–{upper:.1%}"
        ),
    }


def followup_comparison(
    baseline: dict[str, AuditEstimate], updated: dict[str, AuditEstimate]
) -> list[dict[str, Any]]:
    """Explain how adding follow-up audits changes each estimate."""
    result = []
    for frame_id in sorted(set(baseline) | set(updated)):
        before = baseline.get(frame_id)
        after = updated.get(frame_id)
        if before is None or after is None:
            continue
        changed = (
            before.miss_interval != after.miss_interval
            or before.received_weight != after.received_weight
        )
        result.append(
            {
                "frame_id": frame_id,
                "before": {
                    "design_rows": before.n_design,
                    "effective_n": round(before.effective_n, 4),
                    "miss_rate": None if before.miss_rate is None else round(before.miss_rate, 4),
                    "miss_interval": None
                    if before.miss_interval is None
                    else [round(before.miss_interval[0], 4), round(before.miss_interval[1], 4)],
                    "status": before.status,
                },
                "after": {
                    "design_rows": after.n_design,
                    "effective_n": round(after.effective_n, 4),
                    "miss_rate": None if after.miss_rate is None else round(after.miss_rate, 4),
                    "miss_interval": None
                    if after.miss_interval is None
                    else [round(after.miss_interval[0], 4), round(after.miss_interval[1], 4)],
                    "status": after.status,
                },
                "changed": changed,
                "reason": _change_reason(before, after),
            }
        )
    return result


def _change_reason(before: AuditEstimate, after: AuditEstimate) -> str:
    added = after.n_design - before.n_design
    excluded = len(after.excluded_nonprob_rows) - len(before.excluded_nonprob_rows)
    parts = []
    if added > 0:
        parts.append(f"新增 {added} 条概率抽样核查记录")
    if excluded > 0:
        parts.append(f"新增 {excluded} 条非概率记录，但未纳入估计")
    if after.effective_n != before.effective_n:
        parts.append(
            f"有效样本量 {before.effective_n:.2f} → {after.effective_n:.2f}"
            "（不等概率使设计效应变化）"
        )
    if before.miss_rate is not None and after.miss_rate is not None and before.miss_rate != after.miss_rate:
        parts.append(f"估计漏登率 {before.miss_rate:.1%} → {after.miss_rate:.1%}")
    if not parts:
        parts.append("设计型证据没有变化；使用同一批记录与权重")
    return "；".join(parts)
