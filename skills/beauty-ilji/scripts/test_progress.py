#!/usr/bin/env python3
"""Behavior checks for date filtering and whole-plan denominators."""
from datetime import date
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from build_progress import collect, render, svg


class PolicyTests(unittest.TestCase):
    def test_future_completion_keeps_denominator_but_not_numerator(self):
        data = collect(
            "## 오늘\n**일정:** 10/22\n- [x] 검증한 작업 **완료일: 2026-10-22**\n"
            "## 이후\n**일정:** 10/23\n- [x] 이후 작업 **완료일: 2026-10-22**\n",
            date(2026, 10, 22),
        )
        self.assertEqual((data["completed"], data["total"], data["percent"]), (1, 2, 50))
        later = data["groups"][1]["items"][0]
        self.assertFalse(later["checked"])
        self.assertNotIn("완료일", later["text"])

    def test_unchecked_and_fenced_examples_do_not_inflate_progress(self):
        fence = chr(96) * 3
        data = collect(
            f"## 계획\n**일정:** 10/21\n- [x] A\n- [ ] B\n{fence}md\n- [x] 예시\n{fence}\n",
            date(2026, 10, 22),
        )
        self.assertEqual((data["completed"], data["total"]), (1, 2))

    def test_unknown_date_needs_an_explicit_schedule(self):
        text = "## 계획\n- [x] 근거가 있는 항목\n"
        unknown = collect(text, date(2026, 10, 22))
        self.assertEqual(unknown["completed"], 0)
        mapped = collect(text, date(2026, 10, 22), {"T001": "2026-10-22"})
        self.assertEqual(mapped["completed"], 1)

    def test_later_actual_completion_is_not_backdated_into_an_old_report(self):
        data = collect(
            "## 계획\n**일정:** 10/21\n- [x] 작업 **완료일: 2026-10-24**\n",
            date(2026, 10, 22),
        )
        self.assertEqual((data["completed"], data["total"]), (0, 1))

    def test_zero_denominator_is_unknown_not_success(self):
        data = collect("## 계획 없음\n", date(2026, 10, 22))
        self.assertIsNone(data["percent"])
        self.assertIn("0.00", svg(data))

    def test_rendering_preserves_policy_and_escapes_source_markup(self):
        data = collect(
            "## 계획\n**일정:** 10/22\n- [x] <script>alert(1)</script>\n- [ ] 2~5개\n",
            date(2026, 10, 22),
        )
        data.update(source_commit="a" * 40, source_url="https://example.com/todo")
        md, page = render(data)
        self.assertEqual(md.count("- [x]"), 1)
        self.assertEqual(page.count("disabled checked"), 1)
        self.assertIn('value="1" max="2"', page)
        self.assertNotIn("<script>", page)
        self.assertIn(r"2\~5개", md)

    def test_cli_reads_the_pinned_commit_instead_of_dirty_worktree(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder) / "repo"
            repo.mkdir()
            def git(*args):
                return subprocess.check_output(
                    ["git", "-C", str(repo), *args], text=True,
                    stderr=subprocess.DEVNULL,
                ).strip()
            git("init", "--quiet")
            source = repo / "todo.md"
            source.write_text("## 계획\n**일정:** 10/22\n- [x] A\n")
            git("add", "todo.md")
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
            commit = git("rev-parse", "HEAD")
            source.write_text(source.read_text() + "- [x] Uncommitted\n")
            output = Path(folder) / "output"
            subprocess.run([
                sys.executable, str(Path(__file__).with_name("build_progress.py")),
                "--repo", str(repo), "--ref", commit, "--source", "todo.md",
                "--date", "2026-10-22", "--out", str(output),
            ], check=True, stdout=subprocess.DEVNULL)
            result = json.loads((output / "progress.json").read_text())
            self.assertEqual((result["completed"], result["total"]), (1, 1))
            self.assertIn("Uncommitted", source.read_text())


if __name__ == "__main__":
    unittest.main()
