"""Input preparation for the completeness analysis.

Registration semantics are kept separate from opportunity semantics:

* a registration row is resolved per ``observation_id`` by highest revision;
  the latest revision wins and a withdrawn latest revision removes the
  observation from the registry;
* an opportunity row says whether a source actually worked in a region and
  service window.  A source that did not work cannot contribute a capture,
  and a missing appearance there is not evidence that the resident was missed.

No identity matching is performed: the fixture already carries confirmed
``subject_id`` values.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from registry_delta_hub.runtime import DomainError

STATUSES = {"active", "withdrawn"}
COPY_KINDS = {"possible-copy", "confirmed-copy", "copy", "duplicate-feed"}


@dataclass(slots=True)
class RecordResolution:
    rows: list[dict[str, Any]]
    latest: dict[str, dict[str, Any]]
    active: list[dict[str, Any]]
    withdrawn: list[dict[str, Any]]
    superseded_rows: int
    conflicting_revisions: list[dict[str, Any]]
    excluded_inactive_source: list[dict[str, Any]]
    warnings: list[str]


@dataclass(slots=True)
class PreparedData:
    raw: dict[str, Any]
    sources: list[str]
    strata: list[tuple[str, str]]
    active_sources: dict[tuple[str, str], list[str]]
    inactive_sources: dict[tuple[str, str], list[str]]
    records: RecordResolution
    subjects_by_stratum: dict[tuple[str, str], set[str]]
    capture_patterns: dict[tuple[str, str], dict[tuple[str, ...], int]]
    captured_by_source: dict[tuple[str, str], dict[str, set[str]]]
    registered_by_stratum: dict[tuple[str, str], int]
    registered_total: int
    cross_window_subjects: dict[str, list[tuple[str, str]]]
    source_relationships: list[dict[str, Any]]
    frames_by_id: dict[str, dict[str, Any]]
    current_frames: dict[tuple[str, str], dict[str, Any]]
    superseded_frames: list[dict[str, Any]]
    audits: list[dict[str, Any]]
    followup_audits: list[dict[str, Any]]
    audit_budget: int
    frame_caps: dict[str, int]
    warnings: list[str] = field(default_factory=list)

    @property
    def region_windows(self) -> list[str]:
        return [f"{region}/{window}" for region, window in self.strata]


def _require(mapping: Mapping[str, Any], key: str, kind: type, where: str) -> Any:
    if key not in mapping:
        raise DomainError(f"{where}: missing {key}")
    value = mapping[key]
    if kind is int and isinstance(value, bool):
        raise DomainError(f"{where}: {key} must be an integer")
    if not isinstance(value, kind):
        raise DomainError(f"{where}: {key} must be {kind.__name__}")
    return value


def _text(mapping: Mapping[str, Any], key: str, where: str) -> str:
    value = _require(mapping, key, str, where).strip()
    if not value:
        raise DomainError(f"{where}: {key} must not be empty")
    return value


def load_fixture(path: str | Path) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:  # pragma: no cover - message path
        raise DomainError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise DomainError("fixture root must be an object")
    return data


def resolve_records(raw_records: list[dict[str, Any]], sources: set[str]) -> RecordResolution:
    latest: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    warnings: list[str] = []
    for index, row in enumerate(raw_records):
        where = f"records[{index}]"
        if not isinstance(row, dict):
            raise DomainError(f"{where}: must be an object")
        observation_id = _text(row, "observation_id", where)
        _text(row, "subject_id", where)
        source = _text(row, "source_system", where)
        if source not in sources:
            raise DomainError(f"{where}: unknown source {source!r}")
        _text(row, "region", where)
        _text(row, "window", where)
        revision = _require(row, "revision", int, where)
        status = _text(row, "status", where)
        if revision < 1:
            raise DomainError(f"{where}: revision must be >= 1")
        if status not in STATUSES:
            raise DomainError(f"{where}: unknown status {status!r}")
        current = latest.get(observation_id)
        if current is not None and current["revision"] == revision:
            comparable = {k: v for k, v in row.items() if k != "revision"}
            base = {k: v for k, v in current.items() if k != "revision"}
            if comparable != base:
                conflicts.append({"observation_id": observation_id, "revision": revision})
                warnings.append(f"同一修订号下记录冲突：{observation_id}@{revision}")
            continue
        if current is None or revision > current["revision"]:
            latest[observation_id] = dict(row)
    active = [row for row in latest.values() if row["status"] == "active"]
    withdrawn = [row for row in latest.values() if row["status"] == "withdrawn"]
    return RecordResolution(
        rows=list(raw_records),
        latest=latest,
        active=active,
        withdrawn=withdrawn,
        superseded_rows=len(raw_records) - len(latest),
        conflicting_revisions=conflicts,
        excluded_inactive_source=[],
        warnings=warnings,
    )


def _build_patterns(
    records: list[dict[str, Any]],
    strata: list[tuple[str, str]],
    active_sources: dict[tuple[str, str], list[str]],
) -> tuple[
    dict[tuple[str, str], set[str]],
    dict[tuple[str, str], dict[tuple[str, ...], int]],
    dict[tuple[str, str], dict[str, set[str]]],
]:
    subjects: dict[tuple[str, str], set[str]] = {stratum: set() for stratum in strata}
    per_subject: dict[tuple[str, str], dict[str, set[str]]] = {stratum: {} for stratum in strata}
    for row in records:
        key = (row["region"], row["window"])
        if key not in subjects:
            raise DomainError(f"record references unknown region/window {row['region']}/{row['window']}")
        if row["source_system"] not in active_sources[key]:
            # Should have been filtered earlier; kept as a hard guard because a
            # capture from a non-working source would fake a recapture.
            raise DomainError(
                f"active record from inactive source {row['observation_id']} "
                f"({row['region']}/{row['window']}/{row['source_system']})"
            )
        subjects[key].add(row["subject_id"])
        per_subject[key].setdefault(row["subject_id"], set()).add(row["source_system"])
    patterns: dict[tuple[str, str], dict[tuple[str, ...], int]] = {}
    captured: dict[tuple[str, str], dict[str, set[str]]] = {}
    for stratum in strata:
        counts: dict[tuple[str, ...], int] = {}
        sets: dict[str, set[str]] = {source: set() for source in active_sources[stratum]}
        for subject, sources in per_subject[stratum].items():
            key = tuple(sorted(sources))
            counts[key] = counts.get(key, 0) + 1
            for source in sources:
                sets[source].add(subject)
        patterns[stratum] = counts
        captured[stratum] = sets
    return subjects, patterns, captured


def prepare(data: dict[str, Any]) -> PreparedData:
    """Validate and translate a completeness fixture into analysis structures."""
    if not isinstance(data, dict):
        raise DomainError("fixture root must be an object")
    schema = data.get("schema_version")
    if schema != 1:
        raise DomainError(f"unsupported schema_version: {schema!r}")
    raw_sources = data.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise DomainError("sources must be a non-empty list")
    sources: list[str] = []
    for index, source in enumerate(raw_sources):
        if not isinstance(source, str) or not source.strip():
            raise DomainError(f"sources[{index}] must be a non-empty string")
        if source in sources:
            raise DomainError(f"duplicate source: {source}")
        sources.append(source)
    source_set = set(sources)

    raw_records = data.get("records")
    if not isinstance(raw_records, list):
        raise DomainError("records must be a list")
    resolution = resolve_records(raw_records, source_set)

    raw_opportunities = data.get("opportunities")
    if not isinstance(raw_opportunities, list) or not raw_opportunities:
        raise DomainError("opportunities must be a non-empty list")
    opportunities: dict[tuple[str, str, str], bool] = {}
    for index, row in enumerate(raw_opportunities):
        where = f"opportunities[{index}]"
        if not isinstance(row, dict):
            raise DomainError(f"{where}: must be an object")
        region = _text(row, "region", where)
        window = _text(row, "window", where)
        source = _text(row, "source", where)
        if source not in source_set:
            raise DomainError(f"{where}: unknown source {source!r}")
        active = _require(row, "active", bool, where)
        key = (region, window, source)
        if key in opportunities and opportunities[key] != active:
            raise DomainError(f"{where}: conflicting opportunity for {key}")
        opportunities[key] = active

    strata = sorted({(region, window) for region, window, _ in opportunities})
    active_sources: dict[tuple[str, str], list[str]] = {}
    inactive_sources: dict[tuple[str, str], list[str]] = {}
    for stratum in strata:
        active_sources[stratum] = [
            source for source in sources if opportunities.get((stratum[0], stratum[1], source))
        ]
        inactive_sources[stratum] = [
            source for source in sources if not opportunities.get((stratum[0], stratum[1], source))
        ]

    # Remove active observations from sources that were not working before
    # patterns are built; otherwise a closed source would look like a capture.
    excluded: list[dict[str, Any]] = []
    usable: list[dict[str, Any]] = []
    for row in resolution.active:
        key = (row["region"], row["window"])
        if key not in active_sources:
            raise DomainError(f"record references unknown region/window {key}")
        if row["source_system"] not in active_sources[key]:
            excluded.append(dict(row))
            resolution.warnings.append(
                f"来源当期未工作，已排除其活动记录：{row['observation_id']} "
                f"{row['region']}/{row['window']}/{row['source_system']}"
            )
        else:
            usable.append(row)
    resolution.active = usable
    resolution.excluded_inactive_source = excluded

    subjects, patterns, captured = _build_patterns(usable, strata, active_sources)
    registered_by_stratum = {stratum: len(units) for stratum, units in subjects.items()}
    all_subjects: dict[str, list[tuple[str, str]]] = {}
    for stratum, units in subjects.items():
        for subject in units:
            all_subjects.setdefault(subject, []).append(stratum)
    cross_window = {
        subject: sorted(where) for subject, where in all_subjects.items() if len(where) > 1
    }

    relationships = data.get("source_relationships", [])
    if not isinstance(relationships, list):
        raise DomainError("source_relationships must be a list")
    clean_relationships: list[dict[str, Any]] = []
    for index, row in enumerate(relationships):
        where = f"source_relationships[{index}]"
        if not isinstance(row, dict):
            raise DomainError(f"{where}: must be an object")
        source = _text(row, "from", where)
        target = _text(row, "to", where)
        if source not in source_set or target not in source_set:
            raise DomainError(f"{where}: unknown source")
        kind = _text(row, "kind", where)
        if kind not in COPY_KINDS:
            raise DomainError(f"{where}: unsupported relationship {kind!r}")
        clean_relationships.append(
            {
                "from": source,
                "to": target,
                "kind": kind,
                "confirmed": bool(row.get("confirmed", kind != "possible-copy")),
            }
        )

    raw_frames = data.get("audit_frames", [])
    if not isinstance(raw_frames, list):
        raise DomainError("audit_frames must be a list")
    frames_by_id: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(raw_frames):
        where = f"audit_frames[{index}]"
        if not isinstance(row, dict):
            raise DomainError(f"{where}: must be an object")
        frame_id = _text(row, "frame_id", where)
        region = _text(row, "region", where)
        window = _text(row, "window", where)
        eligible = _require(row, "eligible_units", int, where)
        version = _require(row, "version", int, where)
        sampling_unit = row.get("sampling_unit", "service-recipient")
        if not isinstance(sampling_unit, str):
            raise DomainError(f"{where}: sampling_unit must be a string")
        if eligible < 0:
            raise DomainError(f"{where}: eligible_units must be >= 0")
        if version < 1:
            raise DomainError(f"{where}: version must be >= 1")
        if frame_id in frames_by_id:
            raise DomainError(f"duplicate frame_id: {frame_id}")
        if (region, window) not in active_sources:
            raise DomainError(f"{where}: frame references unknown region/window")
        frames_by_id[frame_id] = {
            "frame_id": frame_id,
            "region": region,
            "window": window,
            "eligible_units": eligible,
            "version": version,
            "sampling_unit": sampling_unit,
        }

    warnings = list(resolution.warnings)
    current_frames: dict[tuple[str, str], dict[str, Any]] = {}
    superseded: list[dict[str, Any]] = []
    # Highest version wins.  Equal versions are a data integrity problem, so
    # tie-break on frame_id for determinism and report the loser instead of
    # silently choosing whichever row happened to come first.
    ordered_frames = sorted(
        frames_by_id.values(),
        key=lambda item: (item["region"], item["window"], -item["version"], item["frame_id"]),
    )
    for frame in ordered_frames:
        key = (frame["region"], frame["window"])
        incumbent = current_frames.get(key)
        if incumbent is None or frame["version"] > incumbent["version"]:
            if incumbent is not None:
                superseded.append(incumbent)
                warnings.append(
                    f"抽样框版本被取代：{incumbent['frame_id']} v{incumbent['version']} → "
                    f"{frame['frame_id']} v{frame['version']}"
                )
            current_frames[key] = frame
        else:
            superseded.append(frame)
            if frame["version"] == incumbent["version"]:
                warnings.append(
                    f"{key} 存在同版本抽样框：{incumbent['frame_id']} 与 {frame['frame_id']}；"
                    f"按固定规则保留 {incumbent['frame_id']}"
                )
    if superseded:
        warnings.append("资料含多个抽样框版本；分析对每个地区/窗口使用最高版本")

    def clean_audits(rows: Any, name: str) -> list[dict[str, Any]]:
        if rows is None:
            return []
        if not isinstance(rows, list):
            raise DomainError(f"{name} must be a list")
        cleaned = []
        seen = set()
        for index, row in enumerate(rows):
            where = f"{name}[{index}]"
            if not isinstance(row, dict):
                raise DomainError(f"{where}: must be an object")
            audit_id = _text(row, "audit_id", where)
            frame_id = _text(row, "frame_id", where)
            if audit_id in seen:
                raise DomainError(f"duplicate audit_id: {audit_id}")
            seen.add(audit_id)
            if frame_id not in frames_by_id:
                raise DomainError(f"{where}: unknown frame_id {frame_id!r}")
            probability = row.get("selection_probability")
            if probability is None:
                # A non-probability follow-up (e.g. a targeted hunt for missed
                # people) cannot enter a design-based estimator and is kept out
                # with an explicit flag rather than being treated as random.
                cleaned.append(
                    {
                        "audit_id": audit_id,
                        "frame_id": frame_id,
                        "selection_probability": None,
                        "received_service": bool(row.get("received_service", False)),
                        "found_in_registry": bool(row.get("found_in_registry", False)),
                        "review_revision": row.get("review_revision"),
                        "design_eligible": False,
                    }
                )
                continue
            if isinstance(probability, bool) or not isinstance(probability, (int, float)):
                raise DomainError(f"{where}: selection_probability must be numeric")
            probability = float(probability)
            if not 0.0 < probability <= 1.0:
                raise DomainError(f"{where}: selection_probability must lie in (0, 1]")
            received = _require(row, "received_service", bool, where)
            found = _require(row, "found_in_registry", bool, where)
            if found and not received:
                raise DomainError(f"{where}: found_in_registry=true requires received_service=true")
            cleaned.append(
                {
                    "audit_id": audit_id,
                    "frame_id": frame_id,
                    "selection_probability": probability,
                    "received_service": received,
                    "found_in_registry": found,
                    "review_revision": row.get("review_revision"),
                    "design_eligible": True,
                }
            )
        return cleaned

    audits = clean_audits(data.get("audits", []), "audits")
    followup = clean_audits(data.get("followup_audits", []), "followup_audits")
    baseline_ids = {row["audit_id"] for row in audits}
    overlap_ids = baseline_ids & {row["audit_id"] for row in followup}
    if overlap_ids:
        raise DomainError(f"followup_audits reuse baseline audit ids: {sorted(overlap_ids)}")

    budget = data.get("audit_budget", 0)
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
        raise DomainError("audit_budget must be a non-negative integer")
    raw_caps = data.get("frame_caps", {})
    if not isinstance(raw_caps, dict):
        raise DomainError("frame_caps must be an object")
    caps: dict[str, int] = {}
    for frame_id, value in raw_caps.items():
        if frame_id not in frames_by_id:
            raise DomainError(f"frame_caps references unknown frame: {frame_id}")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise DomainError(f"frame_caps[{frame_id}] must be a non-negative integer")
        caps[frame_id] = value

    return PreparedData(
        raw=data,
        sources=sources,
        strata=strata,
        active_sources=active_sources,
        inactive_sources=inactive_sources,
        records=resolution,
        subjects_by_stratum=subjects,
        capture_patterns=patterns,
        captured_by_source=captured,
        registered_by_stratum=registered_by_stratum,
        registered_total=len(all_subjects),
        cross_window_subjects=cross_window,
        source_relationships=clean_relationships,
        frames_by_id=frames_by_id,
        current_frames=current_frames,
        superseded_frames=superseded,
        audits=audits,
        followup_audits=followup,
        audit_budget=budget,
        frame_caps=caps,
        warnings=warnings,
    )
