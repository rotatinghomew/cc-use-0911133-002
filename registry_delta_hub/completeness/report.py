"""Render a human-readable Chinese report plus a compact machine summary."""
from __future__ import annotations

from typing import Any


def dependence_cn(level: str) -> str:
    return {
        "confirmed-copy": "已确认抄送",
        "suspected-copy": "疑似抄送",
        "mechanical-containment": "机械包含，无独立再捕获",
        "one-sided-containment": "单向包含",
        "none": "未见依赖",
    }.get(level, level)


def estimability_cn(value: str) -> str:
    return {
        "audit-bounded": "核查定界",
        "model-and-audit-consistent": "模型与核查一致",
        "assumption-sensitive": "依赖口径假设，不可单一识别",
        "bounds-only": "仅上下界可估",
        "not-identifiable": "不可识别（仅知下界）",
        "not-identifiable-from-lists": "纯名册不可识别",
    }.get(value, value)


def _fmt(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "—"
    if value == float("inf"):
        return "∞"
    return f"{value:.{digits}f}"


def _interval(pair: list[float] | tuple[float, float] | None, digits: int = 1) -> str:
    if not pair:
        return "—"
    return f"{pair[0]:.{digits}f} – {pair[1]:.{digits}f}"


def _percent_interval(pair: list[float] | tuple[float, float] | None) -> str:
    if not pair:
        return "—"
    return f"{pair[0] * 100:.1f}% – {pair[1] * 100:.1f}%"


def _registered_block(payload: dict[str, Any]) -> list[str]:
    registered = payload["registered"]
    lines = [
        "## 已登记去重人数",
        "",
        f"- 合并行数：{registered['raw_rows']} 行（来源出现次数，不是人数）",
        f"- 按 observation_id 取最高 revision 后：{registered['resolved_observations']} 条，"
        f"其中 {registered['superseded_rows']} 行被修订覆盖、{registered['withdrawn_observations']} 条已撤回",
        f"- 仍在登记的观测：{registered['active_observations']} 条；"
        f"因来源当期未工作而不计入的观测：{registered['active_from_inactive_sources_excluded']} 条",
        f"- **去重居民人数：{registered['distinct_registered_residents']}**",
        "- 分地区/窗口：" + "，".join(
            f"{key} {value}" for key, value in registered["by_stratum"].items()
        ),
        f"- 跨窗口重复出现：{registered['cross_window_residents']['count']} 人"
        + (
            "（未重复累加；窗口是不同服务暴露，不合并为单一封闭总体）"
            if registered["cross_window_residents"]["count"]
            else "（本资料没有同一居民跨窗口出现；仍不把窗口合并成一个封闭总体）"
        ),
        "",
    ]
    return lines


def _opportunity_block(payload: dict[str, Any]) -> list[str]:
    lines = ["## 采集机会", ""]
    for row in payload["opportunities"]["matrix"]:
        lines.append(
            f"- {row['region']}/{row['window']}：工作 "
            + "、".join(row["working_sources"])
            + "；停运/未开展 "
            + ("、".join(row["closed_or_not_operating_sources"]) or "无")
        )
    lines += [
        "",
        "> 来源停运表示没有采集机会，其缺位不是居民漏登；分析只使用当期实际工作的来源。",
        "",
    ]
    return lines


def _stratum_block(stratum: dict[str, Any]) -> list[str]:
    recommendation = stratum["recommendation"]
    lines = [
        f"### {stratum['region']} / {stratum['window']}",
        "",
        f"- 已登记居民：{stratum['registered_residents']}；工作来源：{'、'.join(stratum['active_sources'])}"
        + (
            f"；停运来源：{'、'.join(stratum['inactive_sources'])}"
            if stratum["inactive_sources"]
            else ""
        ),
        "- 来源出现模式：" + "；".join(
            f"{pattern} {count} 人" for pattern, count in stratum["capture_patterns"].items()
        ),
        f"- 来源依赖等级：**{dependence_cn(stratum['dependence']['level'])}**",
    ]
    for evidence in stratum["dependence"]["evidence"]:
        lines.append(f"  - {evidence}")
    if stratum["active_but_no_capture_sources"]:
        lines.append(
            f"- 声明工作但零记录来源：{'、'.join(stratum['active_but_no_capture_sources'])}"
            "（可能是静默停采，不能用其缺位推断漏登）"
        )
    lines += ["", "| 候选模型 | 假设 | 可识别 | 点估计 | 区间 |", "|---|---|---|---|---|"]
    assumption_names = {
        "naive-independence": "两个来源独立采集",
        "copy-aware-independence": "剔除抄送/嵌套来源后独立采集",
        "coverage-extrapolation": "人群采集概率同质",
        "design-free-bounds": "不作独立或同质性假设",
        "audit-calibrated": "核查覆盖率适用于同一服务人群",
    }
    for candidate in stratum["candidates"]:
        lines.append(
            "| {name} | {assumption} | {identified}{deg} | {point} | {interval} |".format(
                name=candidate["name"],
                assumption=assumption_names.get(candidate["name"], candidate["assumption"]),
                identified="是" if candidate["identified"] else "否",
                deg="（零宽退化）" if candidate["degenerate_zero_width"] else "",
                point=_fmt(candidate["point"]),
                interval=_interval(candidate["interval"]),
            )
        )
    if stratum["audit"]:
        audit = stratum["audit"]
        if audit["excluded_nonprobability_rows"]:
            lines.append(
                f"- 本层排除了 {audit['excluded_nonprobability_rows']} 条非概率核查记录，"
                "它们不进入设计估计"
            )
        lines += [
            "",
            f"- 核查抽样框：{audit['frame_id']}（版本 {audit['frame_version']}，"
            f"可核查对象 {audit['eligible_units']}）",
            f"- 设计样本 {audit['design_rows']} 条，有效样本量 {_fmt(audit['effective_n'], 2)}，"
            f"设计效应 {_fmt(audit['design_effect'], 2)}；"
            f"排除非概率核查记录 {audit['excluded_nonprobability_rows']} 条",
            f"- 漏登率（加权）："
            f"{_fmt(None if audit['miss_rate'] is None else audit['miss_rate'] * 100, 1)}%，区间 "
            f"{_percent_interval(audit['miss_interval'])}",
        ]
    if stratum["audit_tests"]:
        lines.append("")
        lines.append("**独立核查对候选模型的检验**")
        verdicts = {
            "consistent-with-audit": "与核查区间相容",
            "model-understates-miss": "模型低估漏登（被核查否定）",
            "model-overstates-miss": "模型高估漏登（被核查否定）",
        }
        for test in stratum["audit_tests"]:
            lines.append(
                f"- {test['candidate']}：{verdicts.get(test['verdict'], test['verdict'])}"
                f"（模型隐含漏登 {test['implied_miss_rate'] * 100:.1f}%，核查区间 "
                f"{test['audit_miss_interval'][0] * 100:.1f}%–{test['audit_miss_interval'][1] * 100:.1f}%）"
            )
    status_cn = {
        "ok": "通过",
        "not-informative": "无法检验",
        "tension-not-conclusive": "存在张力但不足以定论",
        "frame-inconsistent": "与框规模不符",
        "frame-registry-population-mismatch": "框与登记人群不一致",
        "assumption-sensitive": "需并列口径",
    }
    for check in stratum["cross_frame_checks"]:
        if check["status"] != "ok":
            lines.append(
                f"- 跨框检查 {check['check']}：{status_cn.get(check['status'], check['status'])}"
                f" — {check['detail']}"
            )
    if stratum["audit_sensitivity"]:
        lines.append("")
        lines.append("**总体口径敏感性（同一核查的不同翻译）**")
        for item in stratum["audit_sensitivity"]:
            lines.append(f"- {item['assumption']}：{_interval(item['interval'])}")
    lines += [
        "",
        f"**本层结论：{estimability_cn(recommendation['estimability'])}；"
        + (
            f"总人数区间 {_interval(recommendation['total_interval'])}；"
            if recommendation["total_interval"]
            else f"只知道至少 {recommendation['known_lower_bound']} 名已登记居民，无有限上界；"
        )
        + (
            f"可能漏登 {_interval(recommendation['possible_missed_interval'])}**"
            if recommendation["possible_missed_interval"]
            else "**"
        ),
    ]
    for reason in recommendation["reasons"]:
        lines.append(f"- {reason}")
    lines.append("")
    return lines


def _allocation_block(payload: dict[str, Any]) -> list[str]:
    allocation = payload["allocation"]
    lines = [
        "## 有限核查名额建议",
        "",
        f"- 预算 {allocation['budget']} 个名额，已安排 {allocation['summary']['slots_used']} 个；"
        f"预计总体区间总宽度 {_fmt(allocation['summary']['baseline_expected_total_width'])} → "
        f"{_fmt(allocation['summary']['posterior_expected_total_width'])}",
        "",
        "| 层 | 建议名额 | 框上限 | 可抽样余量 | 当前总人数区间 | 核查后预期区间 | 缩窄目标 |",
        "|---|---|---|---|---|---|---|",
    ]
    for slot in allocation["recommendations"]:
        lines.append(
            "| {stratum} | {slots} | {cap} | {headroom} | {before} | {after} | {target} |".format(
                stratum=slot["stratum"],
                slots=slot["recommended_slots"],
                cap=slot["frame_cap"],
                headroom=slot["sampling_headroom"],
                before=_interval(slot["baseline_total_interval"]),
                after=_interval(slot["expected_total_interval_after"]),
                target=slot["target_uncertainty"],
            )
        )
    lines.append("")
    lines.append("**名额逐步理由（按每名额可缩窄的总体区间宽度排序）**")
    for step in allocation["summary"]["incremental_steps"]:
        lines.append(
            f"- 第 {step['slot']} 个名额 → {step['stratum']}：预期宽度 "
            f"{_fmt(step['expected_width_before'])} → {_fmt(step['expected_width_after'])}"
            f"（缩窄 {_fmt(step['expected_width_reduction'])}）；{step['why']}"
        )
    lines += ["", "**约束与不可安排名额的原因**"]
    for constraint in allocation["constraints"]:
        lines.append(f"- {constraint['stratum']}：{constraint['constraint']} — {constraint['detail']}")
    for slot in allocation["recommendations"]:
        if slot["recommended_slots"] == 0 and slot.get("why_zero_slots"):
            lines.append(f"- {slot['stratum']} 未获名额：{slot['why_zero_slots']}")
    lines += [
        "",
        "> 名额按“能缩小哪部分总体不确定性”排序，而不是简单挑漏登率最高的一层；"
        "同时遵守抽样框可核查人数与每框上限。预测宽度是规划量，不是已发布的置信区间。",
        "",
    ]
    return lines


def _followup_block(payload: dict[str, Any]) -> list[str]:
    impact = payload["followup_impact"]
    lines = [
        "## 新核查反馈再入分析：结论为什么改变",
        "",
        f"- 首轮概率核查 {len(impact['baseline_audit_ids'])} 条；新返回 {len(impact['followup_audit_ids'])} 条",
        f"- 新记录是否概率样本：{impact['followup_is_design_based']}",
        "",
    ]
    for change in impact["stratum_changes"]:
        if not change["changed"]:
            lines.append(f"- {change['stratum']}：结论不变（{'；'.join(change['attribution'])}）")
            continue
        lines.append(
            f"- {change['stratum']}：区间 {_interval(change['before']['total_interval'])} → "
            f"{_interval(change['after']['total_interval'])}；原因：{'；'.join(change['attribution'])}"
        )
    lines += ["", "**抽样框版本与模型变化的区分**"]
    domain_cn = {
        "same-frame-version": "同一框版本",
        "frame-version-changed": "框版本变化",
        "mixed-frame-versions": "新旧框混杂",
        "no-frame": "无核查框",
    }
    for row in payload["scenario_sensitivity"]:
        lines.append(
            f"- {row['stratum']}：使用 {row['frame_id'] or '无'}（版本 {row['frame_version_used'] or '—'}）；"
            f"首轮 {row['baseline_audit_rows']} 条、含新反馈 {row['followup_audit_rows']} 条"
            + (
                f"、挂在旧框 {row['audit_rows_on_superseded_frames']} 条"
                if row.get("audit_rows_on_superseded_frames")
                else ""
            )
            + f"；归因：{domain_cn.get(row.get('domain'), row.get('domain'))} — {row['interpretation']}"
        )
    lines.append("")
    return lines


def _candidate_block(payload: dict[str, Any]) -> list[str]:
    lines = [
        "## 候选模型汇总（同一假设跨层）",
        "",
        "| 候选模型 | 有区间的层 | 有点估计的层 | 逐层区间之和 | 逐层点估计之和 | 备注 |",
        "|---|---|---|---|---|---|",
    ]
    names = {
        "naive-independence": "朴素独立",
        "copy-aware-independence": "剔除依赖后独立",
        "coverage-extrapolation": "同质覆盖率外推",
        "design-free-bounds": "设计无关上下界",
        "audit-calibrated": "核查校准",
    }
    for row in payload["candidate_aggregates"]:
        interval = row["sum_of_stratum_intervals"]
        lines.append(
            "| {name} | {n}/{total} | {p}/{total} | {interval} | {point} | {note} |".format(
                name=names.get(row["name"], row["name"]),
                n=row["strata_with_interval"],
                p=row["strata_with_point"],
                total=row["strata_total"],
                interval=_interval(interval, 0) if interval else "—（层不全）",
                point="—" if row["sum_of_points"] is None else f"{row['sum_of_points']:.1f}",
                note="；".join(row["notes"]) or "—",
            )
        )
    lines += [
        "",
        "> 逐层区间之和只是算术和，不是联合置信区间；各层是不同服务窗口。"
        "下文“推荐口径合计”在核查框与登记人群不一致的层并列了多种口径，"
        "因此可能比“核查校准”行更宽；那一行是报告实际采用的保守口径。",
        "",
    ]
    return lines


def _estimability_block(payload: dict[str, Any]) -> list[str]:
    lines = ["## 可估与不可估分层", ""]
    explanations = {
        "audit-bounded": "独立概率核查给出区间，不依赖来源独立假设",
        "model-and-audit-consistent": "列表型模型与核查对漏登比例一致",
        "assumption-sensitive": "核查框与登记窗口可能不是同一人群；并列多种口径",
        "bounds-only": "没有可用概率核查，只剩设计无关上下界",
        "not-identifiable": "没有再捕获对照或没有核查，总体不可识别",
        "not-identifiable-from-lists": "来源抄送或包含使纯名册模型失效",
    }
    for bucket in payload["estimability"]["buckets"]:
        lines.append(
            f"- **{estimability_cn(bucket['estimability'])}**"
            f"（{explanations.get(bucket['estimability'], bucket['explanation'])}）："
            + "、".join(bucket["strata"])
        )
    totals = payload["totals"]
    lines += [
        "",
        f"- 推荐口径合计：总人数 {_interval(totals['sum_of_recommended_total_intervals'])}，"
        f"可能漏登 {_interval(totals['sum_of_possible_missed_intervals'])}"
        f"（已登记 {totals['registered_residents']} 人）",
        f"- 合并行数不能作为总体；合计区间是逐层区间的算术和，不是联合置信区间。",
        "",
    ]
    return lines


def _validation_block(payload: dict[str, Any]) -> list[str]:
    lines = ["## 方法自检（独立小例子）", ""]
    labels = {
        "independent-sources": "独立来源",
        "whole-source-outage": "整源停采",
        "copy-dependency": "来源抄送依赖",
        "unequal-probability-audit": "不等概率核查",
    }
    for item in payload["method_checks"]["scenarios"]:
        status = "通过" if item["passes"] else "未通过"
        lines.append(f"- {labels.get(item['scenario'], item['scenario'])}：{status} — {item['detail']}")
    lines.append("")
    return lines


def render_markdown(payload: dict[str, Any]) -> str:
    lines: list[str] = [
        "# 登记完整性分析",
        "",
        "> 范围：登记完整性证据与补录核查安排；**不是任何人的服务资格结论**，"
        "不用于资格、待遇或个人决定。",
        "> 身份匹配：未开发；subject_id 是已人工确认的统一居民键。",
        "> 原有登记发布流程不受影响，本报告只提供资料完整性证据与补录建议。",
        "",
    ]
    lines += _registered_block(payload)
    lines += _opportunity_block(payload)
    lines += ["## 分层估计与来源依赖", ""]
    for stratum in payload["strata"]:
        lines += _stratum_block(stratum)
    lines += _candidate_block(payload)
    lines += _estimability_block(payload)
    lines += _allocation_block(payload)
    lines += _followup_block(payload)
    lines += _validation_block(payload)
    if payload["warnings"]:
        lines += ["## 数据告警", ""]
        lines += [f"- {warning}" for warning in payload["warnings"]]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def machine_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Compact JSON: the numbers a downstream planner needs."""
    return {
        "registered_residents": payload["registered"]["distinct_registered_residents"],
        "merged_rows": payload["registered"]["raw_rows"],
        "sum_total_interval": payload["totals"]["sum_of_recommended_total_intervals"],
        "sum_possible_missed_interval": payload["totals"]["sum_of_possible_missed_intervals"],
        "estimability_buckets": [
            {"estimability": bucket["estimability"], "strata": bucket["strata"]}
            for bucket in payload["estimability"]["buckets"]
        ],
        "strata": [
            {
                "key": stratum["key"],
                "registered": stratum["registered_residents"],
                "recommended_basis": stratum["recommendation"]["basis"],
                "total_interval": stratum["recommendation"]["total_interval"],
                "possible_missed_interval": stratum["recommendation"]["possible_missed_interval"],
                "estimability": stratum["recommendation"]["estimability"],
                "dependence": stratum["dependence"]["level"],
            }
            for stratum in payload["strata"]
        ],
        "allocation": {
            slot["stratum"]: slot["recommended_slots"]
            for slot in payload["allocation"]["recommendations"]
        },
        "allocation_budget": payload["allocation"]["budget"],
        "method_checks_passed": all(
            item["passes"] for item in payload["method_checks"]["scenarios"]
        ),
    }
