"""候选估计量、来源依赖诊断与核查校准。

每个地区/窗口层比较以下候选：

``naive-independence``
    对重合最多的两个来源做 Chapman 捕获-再捕获。合并名册看起来重合得很好，
    往往正是这个模型造成的；它假设两个来源独立采集。

``copy-aware-independence``
    先剔除已声明抄送、可疑抄送和机械包含（严格超集）的来源，再做 Chapman。
    仍然假设独立，但只在可能互相再捕获的来源之间成立。

``coverage-extrapolation``
    单来源覆盖率外推 ``R^2 / 最大捕获数``。假设每个人的采集概率同质，
    正是地区/人群异质性会打破的那类假设。

``design-free-bounds``
    ``[已登记人数, 核查框可核查对象数]``。不作独立、也不作同质性假设；
    区间宽得诚实。

``audit-calibrated``
    用独立概率核查估计覆盖率，再把已登记人数换算成总数。不假设来源独立采集，
    但假设核查框描述的是同一服务人群。

核查用来“检验”候选，而不是按对已见重合表的贴合程度挑选候选。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

from registry_delta_hub.completeness.audits import (
    AuditEstimate,
    design_based_total_interval,
    found_total_interval,
    implied_total_range,
    model_audit_test,
)
from registry_delta_hub.completeness.data import PreparedData

NAIVE = "naive-independence"
COPY_AWARE = "copy-aware-independence"
COVERAGE = "coverage-extrapolation"
BOUNDS = "design-free-bounds"
AUDIT = "audit-calibrated"


def chapman(n1: int, n2: int, overlap: int) -> tuple[float, float]:
    """Chapman 估计及其近似 95% 半宽。"""
    if overlap <= 0:
        return float("nan"), float("nan")
    estimate = (n1 + 1.0) * (n2 + 1.0) / (overlap + 1.0) - 1.0
    variance = (
        (n1 + 1.0)
        * (n2 + 1.0)
        * (n1 - overlap)
        * (n2 - overlap)
        / ((overlap + 1.0) ** 2 * (overlap + 2.0))
    )
    half_width = 1.96 * math.sqrt(variance) if variance > 0 else 0.0
    return estimate, half_width


def _pair_summary(first: str, second: str, captured: dict[str, set[str]]) -> dict[str, Any]:
    size_first, size_second = len(captured[first]), len(captured[second])
    overlap = len(captured[first] & captured[second])
    smaller = min(size_first, size_second)
    containment = overlap / smaller if smaller else None
    return {
        "sources": [first, second],
        "captured": {first: size_first, second: size_second},
        "overlap": overlap,
        "containment": containment,
        "strict_superset": size_first != size_second and smaller > 0 and overlap == smaller,
        "identical_capture": size_first == size_second and overlap == size_first and size_first > 0,
        "chapman": None if overlap == 0 else chapman(size_first, size_second, overlap)[0],
    }


def _declared_relationships(data: PreparedData, active: list[str]) -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in data.source_relationships
        if item["from"] in active and item["to"] in active
    ]


@dataclass(slots=True)
class Candidate:
    name: str
    assumption: str
    identified: bool
    point: float | None
    interval: tuple[float, float] | None
    degenerate: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "assumption": self.assumption,
            "identified": self.identified,
            "degenerate_zero_width": self.degenerate,
            "point": None if self.point is None else round(self.point, 2),
            "interval": None
            if self.interval is None
            else [round(self.interval[0], 2), round(self.interval[1], 2)],
            "notes": list(self.notes),
        }


@dataclass(slots=True)
class StratumAnalysis:
    region: str
    window: str
    key: str
    registered: int
    active_sources: list[str]
    inactive_sources: list[str]
    active_but_empty_sources: list[str]
    pattern_counts: dict[str, int]
    source_pairs: list[dict[str, Any]]
    relationships: list[dict[str, Any]]
    dependence_level: str
    dependence_evidence: list[str]
    candidates: list[Candidate]
    audit: AuditEstimate | None
    audit_tests: list[dict[str, Any]]
    cross_frame_checks: list[dict[str, Any]]
    audit_sensitivity: list[dict[str, Any]]
    recommendation: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "region": self.region,
            "window": self.window,
            "registered_residents": self.registered,
            "active_sources": self.active_sources,
            "inactive_sources": self.inactive_sources,
            "active_but_no_capture_sources": self.active_but_empty_sources,
            "capture_patterns": self.pattern_counts,
            "source_pairs": self.source_pairs,
            "declared_relationships": self.relationships,
            "dependence": {
                "level": self.dependence_level,
                "evidence": self.dependence_evidence,
            },
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "audit": None if self.audit is None else audit_to_dict(self.audit),
            "audit_tests": self.audit_tests,
            "audit_sensitivity": self.audit_sensitivity,
            "cross_frame_checks": self.cross_frame_checks,
            "recommendation": self.recommendation,
        }


def audit_to_dict(estimate: AuditEstimate) -> dict[str, Any]:
    return {
        "frame_id": estimate.frame_id,
        "frame_version": estimate.frame_version,
        "eligible_units": estimate.eligible_units,
        "design_rows": estimate.n_design,
        "excluded_nonprobability_rows": len(estimate.excluded_nonprob_rows),
        "effective_n": round(estimate.effective_n, 3),
        "design_effect": None if estimate.design_effect is None else round(estimate.design_effect, 3),
        "received_weight": round(estimate.received_weight, 3),
        "missed_weight": round(estimate.missed_weight, 3),
        "miss_rate": None if estimate.miss_rate is None else round(estimate.miss_rate, 4),
        "miss_interval": None
        if estimate.miss_interval is None
        else [round(estimate.miss_interval[0], 4), round(estimate.miss_interval[1], 4)],
        "coverage_interval": None
        if estimate.coverage_interval is None
        else [round(estimate.coverage_interval[0], 4), round(estimate.coverage_interval[1], 4)],
        "status": estimate.status,
        "notes": list(estimate.notes),
    }


def _best_pair(
    sources: list[str], captured: dict[str, set[str]], exclude: set[str]
) -> tuple[str, str] | None:
    usable = [source for source in sources if source not in exclude]
    best: tuple[str, str] | None = None
    best_overlap = -1
    for first, second in combinations(usable, 2):
        overlap = len(captured[first] & captured[second])
        if overlap > best_overlap:
            best_overlap, best = overlap, (first, second)
    return best


def _independence_candidate(
    sources: list[str],
    captured: dict[str, set[str]],
    registered: int,
    frame_eligible: int | None,
    *,
    exclude: set[str],
    name: str,
    assumption: str,
    notes: list[str],
) -> Candidate:
    pair = _best_pair(sources, captured, exclude)
    if pair is None:
        return Candidate(name, assumption, False, None, None, notes=notes + ["可用来源不足两个"])
    first, second = pair
    n1, n2 = len(captured[first]), len(captured[second])
    overlap = len(captured[first] & captured[second])
    if overlap == 0:
        return Candidate(
            name,
            assumption,
            False,
            None,
            None,
            notes=notes + [f"{first}+{second} 没有共同居民，再捕获模型不可识别"],
        )
    estimate, half_width = chapman(n1, n2, overlap)
    lower = max(float(registered), estimate - half_width)
    upper = estimate + half_width
    if frame_eligible is not None:
        upper = min(upper, float(frame_eligible))
        estimate = min(estimate, float(frame_eligible))
    degenerate = half_width == 0.0 and n1 == n2 == overlap
    extra = [f"Chapman 使用 {first}+{second}：捕获 {n1}/{n2}，重合 {overlap}"]
    if degenerate:
        extra.append(
            "重合数等于两个来源各自的捕获数，零宽区间是饱和假象，不能当作“无人漏登”的证据"
        )
    if frame_eligible is not None and estimate >= float(frame_eligible):
        extra.append(f"点估计达到或超过核查框可核查对象数（{frame_eligible}）")
    return Candidate(
        name,
        assumption,
        True,
        estimate,
        (min(lower, upper), max(lower, upper)),
        degenerate,
        notes + extra,
    )


def _coverage_candidate(
    registered: int, captured: dict[str, set[str]], frame_eligible: int | None
) -> Candidate:
    largest = max(captured, key=lambda source: len(captured[source]), default=None)
    if largest is None or not captured[largest]:
        return Candidate(
            COVERAGE,
            "人群采集概率同质",
            False,
            None,
            None,
            notes=["本窗口没有来源捕获到任何居民"],
        )
    capture = len(captured[largest])
    estimate = registered * registered / capture
    if frame_eligible is not None:
        estimate = min(estimate, float(frame_eligible))
    return Candidate(
        COVERAGE,
        "人群采集概率同质",
        True,
        estimate,
        None,
        notes=[
            f"最大工作来源 {largest} 捕获了 {capture}/{registered} 名已见居民；R^2/捕获数 = "
            f"{registered * registered / capture:.1f}",
            "假设从未被任何人见到的人也具有同样采集概率；人群异质性会使其偏低",
        ],
    )


def _bounds_candidate(
    registered: int,
    frame_eligible: int | None,
    largest: str | None,
    captured: dict[str, set[str]],
) -> Candidate:
    if frame_eligible is None:
        return Candidate(
            BOUNDS,
            "除已登记成员外不作假设",
            False,
            float(registered),
            None,
            notes=[
                f"已登记 {registered} 名不同居民，因此服务人群至少为这个数",
                "没有核查框规模，无法给出有限上界",
            ],
        )
    interval = (float(min(registered, frame_eligible)), float(max(registered, frame_eligible)))
    notes = [
        f"下界是 {registered} 名已登记居民；合并后的行数从来不是总体",
        f"上界是核查框可核查对象数 {frame_eligible}，是总体上限而非置信限",
    ]
    if largest is not None and captured[largest]:
        notes.append(
            f"最大来源 {largest} 覆盖了 {len(captured[largest])}/{registered} 名已见居民；"
            "这约束覆盖率，但不产生点估计"
        )
    return Candidate(BOUNDS, "不作独立或同质性假设", True, None, interval, notes=notes)


def analyse_stratum(
    data: PreparedData, region: str, window: str, audit: AuditEstimate | None
) -> StratumAnalysis:
    stratum = (region, window)
    key = f"{region}/{window}"
    active = list(data.active_sources[stratum])
    inactive = list(data.inactive_sources[stratum])
    registered = data.registered_by_stratum[stratum]
    captured = data.captured_by_source[stratum]
    patterns = {
        "+".join(pattern) if pattern else "(无)": count
        for pattern, count in sorted(
            data.capture_patterns[stratum].items(), key=lambda item: (-item[1], item[0])
        )
    }
    empty_active = [source for source in active if not captured[source]]
    frame = data.current_frames.get(stratum)
    frame_eligible = frame["eligible_units"] if frame else None

    relationships = _declared_relationships(data, active)
    pairs = [_pair_summary(first, second, captured) for first, second in combinations(active, 2)]

    # ---- 来源依赖诊断 ---------------------------------------------------------
    evidence: list[str] = []
    declared_dependent: set[str] = set()
    confirmed_copy = False
    for relationship in relationships:
        copier = relationship["to"]
        origin = relationship["from"]
        # A declared copy only matters where the copying feed actually carries
        # records; a declared relation to a silent source is not evidence here.
        if not captured.get(copier):
            evidence.append(
                f"已声明关系 {origin} → {copier}（{relationship['kind']}），"
                f"但 {copier} 在本窗口没有记录，未作为依赖证据使用"
            )
            continue
        declared_dependent.add(copier)
        if relationship["confirmed"]:
            confirmed_copy = True
            evidence.append(
                f"已确认抄送：{origin} → {copier}；{copier} 的重合不是独立再捕获"
            )
        else:
            evidence.append(
                f"未确认的抄送嫌疑：{origin} → {copier}；保守起见把 {copier} 视为依赖来源"
            )
    nested: set[str] = set()
    for pair in pairs:
        if pair["strict_superset"]:
            larger = max(pair["sources"], key=lambda source: pair["captured"][source])
            smaller = min(pair["sources"], key=lambda source: pair["captured"][source])
            nested.add(larger)
            evidence.append(
                f"{larger} 包含了 {smaller} 见到的全部 "
                f"{pair['overlap']} 名居民；其额外重合是机械包含"
            )
            if pair["containment"] is not None and pair["containment"] >= 0.9:
                evidence.append(
                    f"{pair['sources'][0]}/{pair['sources'][1]} 近乎完全包含"
                    f"（{pair['containment']:.0%}），这对来源不能当作再捕获证据"
                )
        elif pair["identical_capture"]:
            nested.update(pair["sources"])
            evidence.append(
                f"{pair['sources'][0]} 与 {pair['sources'][1]} 捕获完全相同的 "
                f"{pair['overlap']} 名居民；两者都不提供独立再捕获"
            )
    if empty_active:
        evidence.append(
            "声明当期工作但没有任何记录："
            + "、".join(empty_active)
            + "（可能是静默停采，不等同于来源关闭）"
        )
    if not evidence:
        evidence.append("未发现已声明抄送、机械包含或静默停采")

    if confirmed_copy:
        level = "confirmed-copy"
    elif declared_dependent:
        level = "suspected-copy"
    elif nested:
        level = "mechanical-containment"
    else:
        level = "none"
    # 不能提供独立第二捕获的来源：被抄送方、被包含方。
    dependent_sources = set(declared_dependent) | set(nested)

    # ---- 候选估计量 -----------------------------------------------------------
    candidates: list[Candidate] = []
    naive = _independence_candidate(
        active, captured, registered, frame_eligible,
        exclude=set(), name=NAIVE,
        assumption="重合最多的两个来源独立采集",
        notes=["忽略抄送声明与包含结构"],
    )
    candidates.append(naive)
    copy_aware = _independence_candidate(
        active, captured, registered, frame_eligible,
        exclude=dependent_sources, name=COPY_AWARE,
        assumption="剔除抄送或包含来源后，剩余来源独立采集",
        notes=(
            ["已剔除依赖来源：" + "、".join(sorted(dependent_sources))]
            if dependent_sources
            else []
        ),
    )
    candidates.append(copy_aware)
    candidates.append(_coverage_candidate(registered, captured, frame_eligible))
    candidates.append(
        _bounds_candidate(
            registered,
            frame_eligible,
            max(captured, key=lambda source: len(captured[source]), default=None),
            captured,
        )
    )
    if audit is not None:
        total_range = implied_total_range(
            registered, audit, float(frame_eligible) if frame_eligible is not None else None
        )
        candidates.append(
            Candidate(
                AUDIT,
                "核查覆盖率适用于与登记名册相同的服务人群",
                total_range is not None,
                None,
                total_range,
                notes=(
                    [
                        f"设计型覆盖率 {audit.coverage_interval[0]:.1%}–{audit.coverage_interval[1]:.1%}，"
                        f"来自 {audit.n_design} 条核查（有效样本量 {audit.effective_n:.1f}）"
                        if audit.coverage_interval
                        else "核查无法校准覆盖率"
                    ]
                    + [
                        "以登记人数为锚的换算：假设已登记居民落在核查框内",
                        "不给点估计；用覆盖率的点估计相除会夸大精度",
                    ]
                ),
            )
        )
    else:
        candidates.append(
            Candidate(
                AUDIT,
                "核查覆盖率适用于同一服务人群",
                False,
                None,
                None,
                notes=["本地区/窗口没有可用的概率核查"],
            )
        )

    # ---- 独立核查对列表型候选的检验 -------------------------------------------
    audit_tests: list[dict[str, Any]] = []
    if audit is not None:
        for candidate in candidates:
            if candidate.name == AUDIT or not candidate.identified:
                continue
            test = model_audit_test(candidate.point, registered, audit)
            if test is None:
                continue
            test = dict(test)
            test["candidate"] = candidate.name
            test["degenerate"] = candidate.degenerate
            if candidate.degenerate:
                test["detail"] += "；候选区间零宽退化，不能视为精确结论"
            audit_tests.append(test)

    # ---- 跨框一致性 -----------------------------------------------------------
    checks: list[dict[str, Any]] = []
    sensitivity: list[dict[str, Any]] = []
    if audit is not None and audit.received_weight > 0:
        served = audit.received_weight
        frame_consistent = frame_eligible is not None and served <= frame_eligible + 1e-9
        if frame_eligible is not None:
            checks.append(
                {
                    "check": "audit-served-vs-frame-size",
                    "status": "ok" if frame_consistent else "frame-inconsistent",
                    "detail": (
                        f"Horvitz-Thompson 服务人数 {served:.1f} "
                        + ("未超过" if frame_consistent else "超过")
                        + f"核查框可核查对象 {frame_eligible}"
                    ),
                }
            )
        implied_coverage = min(1.0, registered / served) if served > 0 else None
        coverage_interval = audit.coverage_interval
        design_total = design_based_total_interval(audit)
        found_total = found_total_interval(audit)
        # Direct reconciliation: the audit's estimated "found in registry"
        # count should be compatible with the number of registered residents in
        # this window.  A found count significantly above it means the frame
        # covers more registered recipients than this window holds (cross-window
        # duplication) or that the frame and the window population differ.
        severe = False
        if found_total is not None and registered > 0:
            severe = registered < found_total[0] - 1e-9
        if found_total is None and registered > 0:
            # No sampled recipient was found in the registry, so the audit
            # cannot upper-bound the found count.  Consistency with the registry
            # is then untestable, not confirmed.
            status = "not-informative"
        elif implied_coverage is not None and coverage_interval is not None:
            if severe:
                status = "frame-registry-population-mismatch"
            elif implied_coverage < coverage_interval[0] or implied_coverage > coverage_interval[1]:
                status = "tension-not-conclusive"
            else:
                status = "ok"
        else:
            status = "not-informative"
        coverage_text = (
            f"登记人数隐含覆盖率 {implied_coverage:.1%}"
            if implied_coverage is not None
            else "登记人数隐含覆盖率不可用"
        )
        interval_text = (
            f"{coverage_interval[0]:.1%}–{coverage_interval[1]:.1%}"
            if coverage_interval is not None
            else "不可用"
        )
        checks.append(
            {
                "check": "audit-found-vs-window-registry",
                "status": status,
                "detail": (
                    "设计型找到人数区间 "
                    + (
                        f"{found_total[0]:.1f}–{found_total[1]:.1f}"
                        if found_total is not None
                        else "不可用（样本中无人已登记）"
                    )
                    + f"，本窗口登记 {registered} 人；{coverage_text}，核查覆盖率区间 {interval_text}"
                    + (
                        "；核查找到的已登记人数显著多于本窗口登记人数，说明跨窗口重复或"
                        "核查框与本窗口人群不一致"
                        if severe
                        else ""
                    )
                ),
            }
        )
        registry_anchored = implied_total_range(
            registered, audit, float(frame_eligible) if frame_eligible is not None else None
        )
        sensitivity.append(
            {
                "assumption": "A：已登记居民落在核查框内",
                "interval": None
                if registry_anchored is None
                else [round(registry_anchored[0], 2), round(registry_anchored[1], 2)],
                "note": "以登记人数为锚的核查换算；跨框检查通过时采用这一口径",
            }
        )
        sensitivity.append(
            {
                "assumption": "B：核查框就是服务人群的权威口径",
                "interval": None
                if design_total is None
                else [round(design_total[0], 2), round(design_total[1], 2)],
                "point": round(served, 2),
                "note": "Horvitz-Thompson 服务总人数及设计型区间；把登记数视为不完整",
            }
        )
        sensitivity.append(
            {
                "assumption": "C：未建立核查框与登记人群的对应关系",
                "interval": [float(registered), float(frame_eligible)]
                if frame_eligible is not None
                else None,
                "note": "只剩设计无关的上下界仍然可辩护",
            }
        )
        if any(check["status"] == "frame-registry-population-mismatch" for check in checks):
            checks.append(
                {
                    "check": "interpretation",
                    "status": "assumption-sensitive",
                    "detail": (
                        "核查仍在其自身框内测量覆盖率，但登记总人数无法由单一比例识别；"
                        "应并列报告 A、B、C 三种口径，而不是一个数字"
                    ),
                }
            )

    # ---- 推荐结论 --------------------------------------------------------------
    reasons: list[str] = []
    mismatch = any(check["status"] == "frame-registry-population-mismatch" for check in checks)
    design_total = design_based_total_interval(audit) if audit is not None else None
    bounds_candidate = next((c for c in candidates if c.name == BOUNDS and c.identified), None)
    bounds_interval = bounds_candidate.interval if bounds_candidate is not None else None
    audit_candidate = next((c for c in candidates if c.name == AUDIT and c.identified), None)
    interval = None
    estimability = "not-identifiable"
    basis = "none"
    if audit_candidate is not None and audit_candidate.interval is not None:
        basis = AUDIT
        interval = audit_candidate.interval
        estimability = "audit-bounded"
        reasons.append("独立概率核查给出覆盖率区间，不依赖“来源独立采集”假设")
    if level in {"suspected-copy", "confirmed-copy", "mechanical-containment"}:
        reasons.append(
            "重合最多的来源之间存在抄送或机械包含，仅凭名册的捕获-再捕获无法识别总体"
        )
    for test in audit_tests:
        if test["candidate"] == NAIVE:
            reasons.append(f"朴素独立模型 vs 核查：{_verdict_cn(test['verdict'])}（{test['detail']}）")
        elif test["candidate"] == COPY_AWARE:
            reasons.append(f"剔除依赖后的独立模型 vs 核查：{_verdict_cn(test['verdict'])}（{test['detail']}）")
        elif test["candidate"] == COVERAGE:
            reasons.append(f"同质覆盖率外推 vs 核查：{_verdict_cn(test['verdict'])}（{test['detail']}）")
    if mismatch:
        estimability = "assumption-sensitive"
        reasons.append("核查框与登记窗口人群不一致；单一核查比例无法识别服务总人数")
        pieces = [piece for piece in (interval, design_total, bounds_interval) if piece]
        if pieces:
            interval = (min(piece[0] for piece in pieces), max(piece[1] for piece in pieces))
            if frame_eligible is not None:
                interval = (
                    min(interval[0], float(frame_eligible)),
                    min(interval[1], float(frame_eligible)),
                )
            reasons.append("推荐区间并列了“登记锚定”“核查框权威”“设计无关上下界”三种口径")
    if audit_candidate is None:
        fallback = next((c for c in candidates if c.name == BOUNDS and c.identified), None)
        if fallback is not None:
            basis = BOUNDS
            interval = fallback.interval
            estimability = "bounds-only"
            reasons.append("没有可用的概率核查，只有设计无关上下界可辩护")
        else:
            basis = "lower-bound-only"
            estimability = "not-identifiable"
            reasons.append(
                f"没有可用核查，也没有核查框规模；只知道至少有 {registered} 名已登记居民，"
                "无法给出有限上界，因此总体不可识别"
            )
    if interval is None and audit is not None and audit.miss_interval is not None and audit.miss_interval[1] >= 1.0:
        reasons.append("抽中者全部未登记，仅凭登记侧无法给出有限上界")
    missed_interval = None
    if interval is not None:
        missed_interval = [
            max(0.0, interval[0] - registered),
            max(0.0, interval[1] - registered),
        ]
        reasons.append(
            "可能漏登区间由总人数区间减去已知已登记人数得到，不是已逐一确认的漏登名单"
        )
    recommendation = {
        "basis": basis,
        "known_lower_bound": registered,
        "total_interval": None
        if interval is None
        else [round(interval[0], 2), round(interval[1], 2)],
        "possible_missed_interval": None
        if missed_interval is None
        else [round(missed_interval[0], 2), round(missed_interval[1], 2)],
        "estimability": estimability,
        "reasons": [reason for reason in reasons if reason],
        "scope_note": "仅关于登记完整性的证据；不是服务资格或任何个人决定",
    }

    return StratumAnalysis(
        region=region,
        window=window,
        key=key,
        registered=registered,
        active_sources=active,
        inactive_sources=inactive,
        active_but_empty_sources=empty_active,
        pattern_counts=patterns,
        source_pairs=pairs,
        relationships=relationships,
        dependence_level=level,
        dependence_evidence=evidence,
        candidates=candidates,
        audit=audit,
        audit_tests=audit_tests,
        cross_frame_checks=checks,
        audit_sensitivity=sensitivity,
        recommendation=recommendation,
    )


def _verdict_cn(verdict: str) -> str:
    return {
        "consistent-with-audit": "与核查区间相容",
        "model-understates-miss": "模型低估漏登，被核查否定",
        "model-overstates-miss": "模型高估漏登，被核查否定",
    }.get(verdict, verdict)


def aggregate_candidates(strata: list[StratumAnalysis]) -> list[dict[str, Any]]:
    order = [NAIVE, COPY_AWARE, COVERAGE, BOUNDS, AUDIT]
    result = []
    for name in order:
        with_interval = 0
        points = []
        lows = []
        highs = []
        notes = []
        for stratum in strata:
            candidate = next(item for item in stratum.candidates if item.name == name)
            if candidate.identified and candidate.interval is not None:
                with_interval += 1
                lows.append(candidate.interval[0])
                highs.append(candidate.interval[1])
            if candidate.identified and candidate.point is not None:
                points.append(candidate.point)
            if candidate.degenerate:
                notes.append(f"{stratum.key}：零宽退化区间，不能视为精确")
        result.append(
            {
                "name": name,
                "strata_with_interval": with_interval,
                "strata_with_point": len(points),
                "strata_total": len(strata),
                "sum_of_stratum_intervals": None
                if with_interval != len(strata)
                else [round(sum(lows), 2), round(sum(highs), 2)],
                "sum_of_points": None
                if len(points) != len(strata)
                else round(sum(points), 2),
                "notes": notes,
                "interval_caveat": "逐层区间之和只是算术和，不是联合置信区间；各层是不同服务窗口，不是同一封闭总体",
            }
        )
    return result
