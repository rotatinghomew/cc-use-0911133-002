"""CLI: ``python3 -m registry_delta_hub.completeness <fixture> --output <dir>``."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from registry_delta_hub.completeness.data import load_fixture
from registry_delta_hub.completeness.pipeline import analyse
from registry_delta_hub.completeness.report import machine_summary, render_markdown
from registry_delta_hub.runtime import DomainError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="登记完整性分析：去重人数、漏登区间、来源依赖、可估分层与核查名额建议"
    )
    parser.add_argument("fixture", help="completeness JSON fixture")
    parser.add_argument(
        "--output", required=True, help="directory for report.md, summary.json, payload.json"
    )
    parser.add_argument(
        "--json", action="store_true", help="print the compact machine summary to stdout as JSON"
    )
    args = parser.parse_args(argv)
    try:
        result = analyse(load_fixture(args.fixture))
    except DomainError as exc:
        print(f"输入无效：{exc}", file=sys.stderr)
        return 2
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    payload = result.payload
    (output / "payload.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary = machine_summary(payload)
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "report.md").write_text(render_markdown(payload), encoding="utf-8")
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    else:
        print(render_markdown(payload), end="")
        print(f"\n输出目录：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
