"""Allocate a limited number of audit slots.

The objective is to shrink **total-population uncertainty**, not to pick the
stratum with the highest observed miss rate.  A stratum with a wide interval
because its audit sample is small can be more informative per slot than a
stratum that already looks bad.

Constraints honoured:
* total budget (``audit_budget``);
* per-frame caps (``frame_caps``);
* the sampling frame: a slot may only go to a frame with eligible units the
  registry has not already accounted for, and never beyond the frame size;
* a frame without a usable probability sample is not a slot candidate, because
  adding slots there would not be design-based.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from registry_delta_hub.completeness.audits import (
    AuditEstimate,
    audit_weights,
    implied_total_range,
)
from registry_delta_hub.completeness.models import StratumAnalysis
from registry_delta_hub.completeness.stats import beta_binomial_pmf, clopper_pearson


@dataclass(slots=True)
class FrameAllocationState:
    frame_id: str
    stratum_key: str
    eligible_units: int
    registered: int
    received_weight: float
    missed_weight: float
    effective_n: float
    design_probability: float
    received_weights: list[float]
    headroom: int
    cap: int
    baseline_interval: tuple[float, float] | None
    allocation: int = 0
    expected_interval: tuple[float, float] | None = None


def _design_probability(estimate: AuditEstimate) -> float:
    probabilities = [row["selection_probability"] for row in estimate.design_rows]
    if not probabilities:
        return 0.0
    return sum(probabilities) / len(probabilities)


def _posterior_parameters(state: FrameAllocationState) -> tuple[float, float]:
    # Jeffreys-smoothed successes/failures on the effective sample scale keeps
    # the predictive honest when the design effect is large.
    if state.received_weight <= 0:
        return 0.5, 0.5
    found_share = 1.0 - state.missed_weight / state.received_weight
    successes = max(0.0, found_share * state.effective_n)
    failures = max(0.0, state.effective_n - successes)
    return successes + 0.5, failures + 0.5


def _interval_after(
    state: FrameAllocationState, extra_found: float, extra_missed: float
) -> tuple[float, float] | None:
    # New draws are assumed to carry the frame's average inclusion probability;
    # this is a planning approximation and is labelled as such in the summary.
    weight = 1.0 / state.design_probability if state.design_probability > 0 else 0.0
    added = extra_found + extra_missed
    received = state.received_weight + added * weight
    missed = state.missed_weight + extra_missed * weight
    if received <= 0:
        return None
    # Kish effective size among the *received* domain, extended with the new
    # weighted rows.  Using the whole-sample effective size here would mix
    # non-recipients into the coverage ratio.
    total_weight_new = sum(state.received_weights) + added * weight
    sumsq_new = sum(w * w for w in state.received_weights) + added * weight * weight
    effective_new = total_weight_new * total_weight_new / sumsq_new if sumsq_new > 0 else 0.0
    if effective_new <= 0:
        return None
    miss_rate = missed / received
    lower, upper = clopper_pearson(miss_rate * effective_new, effective_new)
    cap = float(state.eligible_units)
    lower_total = state.registered if lower <= 0 else state.registered / (1.0 - lower)
    upper_total = cap if upper >= 1.0 else state.registered / (1.0 - upper)
    lower_total = min(max(lower_total, float(state.registered)), cap)
    upper_total = min(max(upper_total, lower_total), cap)
    return lower_total, upper_total


def _expected_width(state: FrameAllocationState, slots: int) -> float:
    if state.baseline_interval is None:
        return float("inf")
    if slots <= 0:
        return state.baseline_interval[1] - state.baseline_interval[0]
    a, b = _posterior_parameters(state)
    expected = 0.0
    for found in range(slots + 1):
        probability = beta_binomial_pmf(found, slots, a, b)
        interval = _interval_after(state, float(found), float(slots - found))
        if interval is None:
            return float("inf")
        expected += probability * (interval[1] - interval[0])
    return expected


def build_states(
    strata: list[StratumAnalysis],
    budget: int,
    frame_caps: dict[str, int],
) -> list[FrameAllocationState]:
    states: list[FrameAllocationState] = []
    for stratum in strata:
        audit = stratum.audit
        if audit is None or audit.miss_interval is None or audit.status in {"no-probability-sample", "no-service-recipients"}:
            continue
        frame_id = audit.frame_id
        eligible = audit.eligible_units
        headroom = max(0, eligible - stratum.registered)
        # The baseline must be the interval actually recommended, otherwise the
        # planner would "shrink" an interval the report does not publish.
        recommended = stratum.recommendation.get("total_interval")
        interval = (
            (recommended[0], recommended[1])
            if recommended
            else implied_total_range(stratum.registered, audit, float(eligible))
        )
        # A frame/registry population mismatch is a frame-scope problem.  More
        # draws from the same frame cannot resolve it, so no slots are offered.
        if stratum.recommendation.get("estimability") == "assumption-sensitive":
            cap = 0
        else:
            cap = min(frame_caps.get(frame_id, budget), budget, headroom)
        if cap <= 0 and stratum.recommendation.get("estimability") != "assumption-sensitive":
            continue
        states.append(
            FrameAllocationState(
                frame_id=frame_id,
                stratum_key=stratum.key,
                eligible_units=eligible,
                registered=stratum.registered,
                received_weight=audit.received_weight,
                missed_weight=audit.missed_weight,
                effective_n=audit.effective_n,
                design_probability=_design_probability(audit),
                received_weights=[
                    weight for weight, row in zip(audit_weights(audit.design_rows), audit.design_rows)
                    if row["received_service"]
                ],
                headroom=headroom,
                cap=cap,
                baseline_interval=interval,
                expected_interval=interval,
            )
        )
    return states


def allocate(
    strata: list[StratumAnalysis], budget: int, frame_caps: dict[str, int]
) -> tuple[list[dict[str, Any]], list[FrameAllocationState], dict[str, Any]]:
    states = build_states(strata, budget, frame_caps)
    remaining = budget
    allocation: dict[str, int] = {state.frame_id: 0 for state in states}
    current = {state.frame_id: _expected_width(state, 0) for state in states}
    baseline_total = sum(current.values())
    steps: list[dict[str, Any]] = []
    target = {state.frame_id: "upper end of total interval" for state in states}
    while remaining > 0:
        best: FrameAllocationState | None = None
        best_gain = 0.0
        best_width = 0.0
        for state in states:
            if allocation[state.frame_id] >= state.cap:
                continue
            candidate_width = _expected_width(state, allocation[state.frame_id] + 1)
            gain = current[state.frame_id] - candidate_width
            if gain > best_gain + 1e-12:
                best, best_gain, best_width = state, gain, candidate_width
        if best is None:
            break
        allocation[best.frame_id] += 1
        previous = current[best.frame_id]
        current[best.frame_id] = best_width
        remaining -= 1
        steps.append(
            {
                "slot": sum(allocation.values()),
                "frame_id": best.frame_id,
                "stratum": best.stratum_key,
                "expected_width_reduction": round(best_gain, 3),
                "expected_width_before": round(previous, 3),
                "expected_width_after": round(best_width, 3),
                "why": _why(best, previous, best_width),
            }
        )
    after_total = sum(current.values())
    slots = []
    for state in states:
        interval = _expected_interval_after_random(state, allocation[state.frame_id])
        slots.append(
            {
                "frame_id": state.frame_id,
                "stratum": state.stratum_key,
                "recommended_slots": allocation[state.frame_id],
                "frame_cap": frame_caps.get(state.frame_id),
                "sampling_headroom": state.headroom,
                "registered": state.registered,
                "eligible_units": state.eligible_units,
                "baseline_total_interval": None
                if state.baseline_interval is None
                else [round(state.baseline_interval[0], 2), round(state.baseline_interval[1], 2)],
                "expected_total_interval_after": None
                if interval is None
                else [round(interval[0], 2), round(interval[1], 2)],
                "expected_width_reduction": round(
                    (state.baseline_interval[1] - state.baseline_interval[0])
                    - (_expected_width(state, allocation[state.frame_id])),
                    3,
                )
                if state.baseline_interval is not None
                else None,
                "target_uncertainty": _target_uncertainty(state, strata),
                "why_zero_slots": _why_zero(state, allocation[state.frame_id]),
            }
        )
    summary = {
        "budget": budget,
        "slots_used": sum(allocation.values()),
        "slots_unused": budget - sum(allocation.values()),
        "baseline_expected_total_width": round(baseline_total, 3),
        "posterior_expected_total_width": round(after_total, 3),
        "expected_reduction": round(baseline_total - after_total, 3),
        "incremental_steps": steps,
        "predictive_approximation": (
            "新名额按该框现有平均入样概率近似加权；实际抽样的 p 可能不同，"
            "因此这里只用于排序名额，不作为最终区间。"
        ),
        "note": (
            "预期宽度用核查有效样本上的 beta-二项预测分布计算；"
            "这些是规划量，不是已发布的置信区间。"
        ),
    }
    return slots, states, summary


def _expected_interval_after_random(state: FrameAllocationState, slots: int) -> tuple[float, float] | None:
    if slots <= 0:
        return state.baseline_interval
    a, b = _posterior_parameters(state)
    low = high = 0.0
    for found in range(slots + 1):
        probability = beta_binomial_pmf(found, slots, a, b)
        interval = _interval_after(state, float(found), float(slots - found))
        if interval is None:
            return None
        low += probability * interval[0]
        high += probability * interval[1]
    return low, high


def _target_uncertainty(state: FrameAllocationState, strata: list[StratumAnalysis]) -> str:
    stratum = next((item for item in strata if item.key == state.stratum_key), None)
    if stratum is not None and stratum.recommendation["estimability"] == "assumption-sensitive":
        return "核查框与登记人群口径不一致；增加抽样无法消除这部分不确定性"
    if state.missed_weight <= 0:
        return "服务总人数的上界，当前受有效样本量偏小限制"
    return "核查覆盖率下界，它决定总人数的下界"


def _why_zero(state: FrameAllocationState, slots: int) -> str | None:
    if slots > 0:
        return None
    if state.cap <= 0:
        return "0 个名额：核查框与登记人群口径不一致属于框范围问题，同一框内再抽样无法缩小它"
    return "0 个名额：在该框每个可选步骤上，其他层每名额可缩窄的总人数区间都更大"


def _why(state: FrameAllocationState, before: float, after: float) -> str:
    if state.missed_weight <= 0:
        return (
            f"{state.stratum_key} 尚未观察到漏登者，总人数上界主要由偏小的有效样本量决定"
            f"（n_eff {state.effective_n:.1f}）；再抽一条概率样本主要压缩上界"
        )
    coverage_low = max(0.0, 1.0 - state.missed_weight / state.received_weight)
    return (
        f"{state.stratum_key} 观察到的漏登接近全部（覆盖率 {coverage_low:.0%}），"
        "总人数下界取决于真实覆盖率可能低到什么程度；再抽一条可抬高下界"
    )


def allocation_constraints(
    strata: list[StratumAnalysis], budget: int, frame_caps: dict[str, int]
) -> list[dict[str, Any]]:
    notes = []
    for stratum in strata:
        audit = stratum.audit
        if audit is None:
            notes.append(
                {
                    "stratum": stratum.key,
                    "constraint": "no-audit-frame",
                    "detail": "本层没有核查抽样框，无法安排核查名额",
                }
            )
            continue
        headroom = audit.eligible_units - stratum.registered
        mismatch = stratum.recommendation.get("estimability") == "assumption-sensitive"
        if mismatch:
            constraint = "frame-scope-mismatch"
            detail = (
                f"{audit.eligible_units} 个可核查对象，已登记 {stratum.registered} 人；"
                "核查框与登记窗口人群不一致，增加抽样不能消除这一口径分歧"
            )
        elif headroom <= 0:
            constraint = "no-sampling-headroom"
            detail = f"可核查对象 {audit.eligible_units} 已不大于已登记 {stratum.registered} 人"
        elif frame_caps.get(audit.frame_id, 0) <= 0:
            constraint = "frame-cap-zero"
            detail = f"该框本季度名额上限为 0"
        else:
            constraint = "usable"
            detail = (
                f"可核查对象 {audit.eligible_units}，已登记 {stratum.registered}，"
                f"可抽样余量 {max(0, headroom)}，框上限 {frame_caps.get(audit.frame_id, 0)}"
            )
        notes.append(
            {
                "stratum": stratum.key,
                "frame_id": audit.frame_id,
                "frame_cap": frame_caps.get(audit.frame_id),
                "sampling_headroom": max(0, headroom),
                "constraint": constraint,
                "detail": detail,
            }
        )
    return notes
