"""CLI 输出、报告内容与原有发布流程不变。"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from registry_delta_hub.completeness.__main__ import main

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "fixtures/completeness.json"


class CliTests(unittest.TestCase):
    def test_cli_writes_three_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            code = main([str(FIXTURE), "--output", directory])
            self.assertEqual(code, 0)
            for name in ("report.md", "summary.json", "payload.json"):
                self.assertTrue((Path(directory) / name).exists(), name)
            summary = json.loads((Path(directory) / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["registered_residents"], 71)
            self.assertEqual(summary["merged_rows"], 153)
            self.assertEqual(summary["allocation_budget"], 12)
            self.assertTrue(summary["method_checks_passed"])

    def test_cli_json_flag_matches_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-m", "registry_delta_hub.completeness",
                 str(FIXTURE), "--output", directory, "--json"],
                cwd=ROOT, capture_output=True, text=True, check=True,
            )
            stdout_summary = json.loads(result.stdout)
            file_summary = json.loads(
                (Path(directory) / "summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(stdout_summary, file_summary)

    def test_invalid_fixture_returns_usage_error(self):
        with tempfile.TemporaryDirectory() as directory:
            bad = Path(directory) / "bad.json"
            bad.write_text("{}", encoding="utf-8")
            self.assertEqual(main([str(bad), "--output", directory]), 2)

    def test_report_states_scope_and_required_sections(self):
        with tempfile.TemporaryDirectory() as directory:
            main([str(FIXTURE), "--output", directory])
            report = (Path(directory) / "report.md").read_text(encoding="utf-8")
            self.assertIn("已登记去重人数", report)
            self.assertIn("**去重居民人数：71**", report)
            self.assertIn("可能漏登", report)
            self.assertIn("来源依赖等级", report)
            self.assertIn("可估与不可估分层", report)
            self.assertIn("有限核查名额建议", report)
            self.assertIn("不是任何人的服务资格结论", report)
            self.assertIn("原有登记发布流程不受影响", report)
            self.assertIn("独立来源", report)
            self.assertIn("整源停采", report)
            self.assertIn("来源抄送依赖", report)
            self.assertIn("不等概率核查", report)


class ExistingPipelineTests(unittest.TestCase):
    def test_original_entrypoint_still_works(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-m", "registry_delta_hub.console.run",
                 "fixtures/demo.json", "--db", str(Path(directory) / "state.sqlite3")],
                cwd=ROOT, capture_output=True, text=True, check=True,
            )
            response = json.loads(result.stdout)
            self.assertEqual(response["state"], "completed")
            self.assertTrue(response["replayed"] is False)

    def test_completeness_fixture_is_not_routed_to_old_entrypoint(self):
        # The old entrypoint expects a single record; the batch fixture must not
        # silently be accepted as one.
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-m", "registry_delta_hub.console.run",
                 "fixtures/completeness.json", "--db", str(Path(directory) / "state.sqlite3")],
                cwd=ROOT, capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
