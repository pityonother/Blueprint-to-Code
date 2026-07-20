import sys
import tempfile
import unittest
import random
from unittest.mock import patch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.context_pack import estimate_tokens  # noqa: E402
from blueprint_translator.report_query import (  # noqa: E402
    MAX_REPORT_QUERY_BUDGET,
    build_report_view,
    parse_markdown_sections,
    resolve_report_path,
)


MARKDOWN_REPORT = """# Asset Report
FULL_BODY_SENTINEL root introduction
## Summary
FULL_BODY_SENTINEL summary details
```python
# This is a comment, not a heading
## Neither is this
```
### Details
FULL_BODY_SENTINEL nested details
## Function Calls
FULL_BODY_SENTINEL call details
# Appendix
FULL_BODY_SENTINEL appendix details"""


class ReportQueryTests(unittest.TestCase):
    def test_outline_returns_markdown_headings_and_inclusive_line_ranges_only(self):
        result = build_report_view(MARKDOWN_REPORT, mode="outline")

        self.assertEqual(
            parse_markdown_sections(MARKDOWN_REPORT),
            [
                {"title": "Asset Report", "level": 1, "start_line": 1, "end_line": 12},
                {"title": "Summary", "level": 2, "start_line": 3, "end_line": 10},
                {"title": "Details", "level": 3, "start_line": 9, "end_line": 10},
                {"title": "Function Calls", "level": 2, "start_line": 11, "end_line": 12},
                {"title": "Appendix", "level": 1, "start_line": 13, "end_line": 14},
            ],
        )
        self.assertEqual(result["sections"], [])
        self.assertIn("# Asset Report", result["content"])
        self.assertIn("## Summary", result["content"])
        self.assertIn("### Details", result["content"])
        self.assertIn("## Function Calls", result["content"])
        self.assertIn("# Appendix", result["content"])
        self.assertNotIn("FULL_BODY_SENTINEL", result["content"])

        for section in parse_markdown_sections(MARKDOWN_REPORT):
            expected_range = f'{section["start_line"]}-{section["end_line"]}'
            self.assertIn(expected_range, result["content"])

    def test_outline_total_lines_matches_splitlines_with_a_trailing_newline(self):
        result = build_report_view("# Heading\n", mode="outline")

        self.assertEqual(result["total_lines"], 1)

    def test_section_stops_before_the_next_heading_at_the_same_or_higher_level(self):
        report = """# Root
root body
## Target
target opening
### Child
child body
#### Deep Child
deep body
## Next Sibling
must not be returned
# Appendix
must not be returned either"""

        result = build_report_view(report, mode="section", section="Target")

        self.assertEqual(
            result["content"].splitlines(),
            [
                "## Target",
                "target opening",
                "### Child",
                "child body",
                "#### Deep Child",
                "deep body",
            ],
        )
        self.assertNotIn("Next Sibling", result["content"])
        self.assertNotIn("Appendix", result["content"])

    def test_search_returns_deduplicated_overlapping_context_with_line_numbers(self):
        report = """unrelated opening
before first match
Needle first match
bridge Needle second match
after second match
unrelated ending"""

        result = build_report_view(
            report,
            mode="search",
            query="needle",
            context_lines=1,
        )

        self.assertEqual(
            result["content"].splitlines(),
            [
                "L2: before first match",
                "L3: Needle first match",
                "L4: bridge Needle second match",
                "L5: after second match",
            ],
        )
        returned_line_numbers = [
            line.split(":", 1)[0] for line in result["content"].splitlines()
        ]
        self.assertEqual(len(returned_line_numbers), len(set(returned_line_numbers)))

    def test_full_view_paginates_without_gaps_or_duplicates_and_never_exceeds_budget(self):
        source_lines = [
            f"line {index:02d} alpha beta gamma delta epsilon"
            for index in range(1, 10)
        ]
        report = "\n".join(source_lines)
        token_budget = 30
        cursor = 0
        returned_lines: list[str] = []

        for _ in range(len(source_lines)):
            result = build_report_view(
                report,
                mode="full",
                cursor=cursor,
                token_budget=token_budget,
            )

            self.assertLessEqual(estimate_tokens(result["content"]), token_budget)
            self.assertEqual(result["estimated_tokens"], estimate_tokens(result["content"]))
            returned_lines.extend(result["content"].splitlines())

            if not result["truncated"]:
                self.assertIsNone(result["next_cursor"])
                break

            self.assertIsInstance(result["next_cursor"], int)
            self.assertGreater(result["next_cursor"], cursor)
            cursor = result["next_cursor"]
        else:
            self.fail("full view pagination did not reach the end of the report")

        self.assertEqual(returned_lines, source_lines)

    def test_full_view_continues_an_oversized_single_line_without_losing_characters(self):
        report = "oversized-" + ("x" * 400)
        cursor = 0
        pages: list[str] = []

        for _ in range(100):
            result = build_report_view(report, mode="full", cursor=cursor, token_budget=12)
            pages.append(result["content"])
            self.assertLessEqual(estimate_tokens(result["content"]), 12)
            if not result["truncated"]:
                break
            cursor = result["next_cursor"]
        else:
            self.fail("oversized line pagination did not finish")

        self.assertEqual("".join(pages), report)

    def test_core_clamps_untrusted_budget_even_when_the_cli_requests_more(self):
        report = "\n".join(f"line {index} with content" for index in range(10000))

        result = build_report_view(report, mode="full", token_budget=10**9)

        self.assertLessEqual(result["estimated_tokens"], MAX_REPORT_QUERY_BUDGET)
        self.assertTrue(result["truncated"])

    def test_full_mode_does_not_parse_the_entire_markdown_outline(self):
        with patch(
            "blueprint_translator.report_query.parse_markdown_sections",
            side_effect=AssertionError("full mode must stay lazy"),
        ):
            result = build_report_view("one\ntwo\nthree", mode="full", token_budget=10)

        self.assertIn("one", result["content"])

    def test_markdown_parser_ignores_hash_lines_inside_fenced_code_blocks(self):
        sections = parse_markdown_sections(MARKDOWN_REPORT)

        self.assertEqual(
            [section["title"] for section in sections],
            ["Asset Report", "Summary", "Details", "Function Calls", "Appendix"],
        )
        self.assertNotIn("This is a comment, not a heading", sections)
        self.assertNotIn("Neither is this", sections)

    def test_markdown_parser_honors_fence_length_and_preserves_csharp_heading(self):
        report = """````python
```
## fake heading inside four-backtick fence
````
## C#"""

        sections = parse_markdown_sections(report)

        self.assertEqual([item["title"] for item in sections], ["C#"])

    def test_duplicate_section_title_requires_an_explicit_start_line(self):
        report = """# Root
## Damage
first
## Damage
second"""

        with self.assertRaisesRegex(ValueError, "ambiguous"):
            build_report_view(report, mode="section", section="Damage")
        selected = build_report_view(
            report,
            mode="section",
            section="Damage",
            section_start_line=4,
        )

        self.assertEqual(selected["content"].splitlines(), ["## Damage", "second"])

    def test_outline_cursor_preserves_an_oversized_heading_without_ellipsis_loss(self):
        title = "VeryLong" + ("x" * 1000)
        report = f"# {title}"
        cursor = 0
        pages: list[str] = []

        for _ in range(100):
            result = build_report_view(report, mode="outline", cursor=cursor, token_budget=20)
            pages.append(result["content"])
            if not result["truncated"]:
                break
            cursor = result["next_cursor"]
        else:
            self.fail("outline pagination did not finish")

        self.assertEqual("".join(pages), f"# {title} (lines 1-1)")

    def test_whitespace_only_full_view_is_still_bounded(self):
        result = build_report_view(" " * 1_000_000, mode="full", token_budget=1)

        self.assertLessEqual(result["estimated_tokens"], 1)
        self.assertLessEqual(len(result["content"]), 12)
        self.assertTrue(result["truncated"])

    def test_random_full_pages_never_exceed_the_shared_estimator_budget(self):
        rng = random.Random(20260711)
        alphabet = "abcXYZ_  \n\t中!?"
        for _case in range(250):
            report = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 500)))
            budget = rng.randint(1, 80)
            result = build_report_view(report, mode="full", token_budget=budget)
            self.assertLessEqual(estimate_tokens(result["content"]), budget)
            self.assertTrue(report.startswith(result["content"]))

    def test_report_path_resolution_accepts_known_targets_and_blocks_traversal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir) / "Asset"
            output_dir = asset_dir / "output"
            output_dir.mkdir(parents=True)
            expected = output_dir / "asset_report.md"
            expected.write_text("# Report", encoding="utf-8")
            json_payload = output_dir / "asset.json"
            json_payload.write_text("{}", encoding="utf-8")

            self.assertEqual(resolve_report_path(asset_dir, "asset_report"), expected.resolve())
            with self.assertRaises(ValueError):
                resolve_report_path(asset_dir, "../outside.md")
            with self.assertRaisesRegex(ValueError, "Markdown"):
                resolve_report_path(asset_dir, "output/asset.json")


if __name__ == "__main__":
    unittest.main()
