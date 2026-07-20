"""Budgeted Markdown report views for agents and local API clients."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .context_pack import ASCII_CHARS_PER_TOKEN, SPACE_CHARS_PER_TOKEN, estimate_tokens


DEFAULT_REPORT_QUERY_BUDGET = 1200
MAX_REPORT_QUERY_BUDGET = 8000
MAX_REPORT_CONTEXT_LINES = 20
MAX_REPORT_CHARACTERS_PER_TOKEN = 12
MAX_REPORT_FILE_BYTES = 32 * 1024 * 1024

REPORT_FILES = {
    "agent_index": ("output", "agent_index.md"),
    "next_actions": ("output", "next_actions.md"),
    "notes_todo": ("output", "notes_todo.md"),
    "behavior_summary": ("output", "behavior_summary.md"),
    "context_review": ("output", "context_review.md"),
    "asset_memory_card": ("output", "asset_memory_card.md"),
    "context_pack": ("output", "context_pack.md"),
    "formula_candidates": ("output", "formula_candidates.md"),
    "capture_quality_report": ("output", "capture_quality_report.md"),
    "diagnostics_report": ("output", "diagnostics_report.md"),
    "asset_report": ("output", "asset_report.md"),
    "call_graph_summary": ("output", "call_graph_summary.md"),
    "uasset_graph_read_report": ("uasset_graph_read_report.md",),
    "uasset_property_parse_report": ("uasset_property_parse_report.md",),
    "uasset_link_resolution_report": ("uasset_link_resolution_report.md",),
    "uasset_partial_graph_triage": ("uasset_partial_graph_triage.md",),
    "uasset_quality_gates": ("uasset_quality_gates.md",),
    "uasset_vs_clipboard_compare": ("uasset_vs_clipboard_compare.md",),
    "uasset_structure_report": ("uasset_structure_report.md",),
    "uasset_class_defaults_report": ("uasset_class_defaults_report.md",),
}


def resolve_report_path(asset_dir: Path, report: str) -> Path:
    """Resolve a known report key or safe asset-relative report path."""
    root = Path(asset_dir).expanduser().resolve()
    requested = str(report or "").strip()
    relative = Path(*REPORT_FILES[requested]) if requested in REPORT_FILES else Path(requested)
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise ValueError("report path must stay inside the asset directory")
    if target.suffix.casefold() != ".md":
        raise ValueError("only Markdown report files are supported")
    if not target.is_file():
        raise FileNotFoundError(f"report file does not exist: {target}")
    if target.stat().st_size > MAX_REPORT_FILE_BYTES:
        raise ValueError(f"Markdown report exceeds the {MAX_REPORT_FILE_BYTES}-byte safety limit")
    return target


def parse_markdown_sections(text: str) -> list[dict[str, object]]:
    """Return Markdown headings and their inclusive section line ranges."""
    lines = str(text or "").splitlines()
    headings: list[dict[str, object]] = []
    fence_marker = ""
    fence_length = 0
    for index, line in enumerate(lines):
        if fence_marker:
            closing = re.match(r"^\s{0,3}(`{3,}|~{3,})\s*$", line)
            if closing and closing.group(1)[0] == fence_marker and len(closing.group(1)) >= fence_length:
                fence_marker = ""
                fence_length = 0
            continue
        opening = re.match(r"^\s{0,3}(`{3,}|~{3,})(?:[^`~].*)?$", line)
        if opening:
            fence_marker = opening.group(1)[0]
            fence_length = len(opening.group(1))
            continue
        match = re.match(r"^(#{1,6})[ \t]+(.+?)\s*$", line)
        if not match:
            continue
        title = re.sub(r"[ \t]+#+[ \t]*$", "", match.group(2)).strip()
        headings.append(
            {
                "title": title,
                "level": len(match.group(1)),
                "start_line": index + 1,
                "end_line": len(lines),
            }
        )

    for index, heading in enumerate(headings):
        level = int(heading["level"])
        for following in headings[index + 1 :]:
            if int(following["level"]) <= level:
                heading["end_line"] = int(following["start_line"]) - 1
                break
    return headings


def _prefix_length_for_budget(text: str, start: int, token_budget: int) -> int:
    total = 0
    ascii_run = 0
    space_run = 0
    accepted = 0
    last_newline = 0
    char_limit = max(token_budget * MAX_REPORT_CHARACTERS_PER_TOKEN, 1)

    def run_cost(length: int, width: int) -> int:
        return max(1, (length + width - 1) // width) if length else 0

    for index in range(start, min(len(text), start + char_limit)):
        char = text[index]
        if char.isascii() and (char.isalnum() or char == "_"):
            if space_run:
                total += run_cost(space_run, SPACE_CHARS_PER_TOKEN)
                space_run = 0
            ascii_run += 1
        elif char == "\n":
            total += run_cost(ascii_run, ASCII_CHARS_PER_TOKEN) + run_cost(space_run, SPACE_CHARS_PER_TOKEN) + 1
            ascii_run = 0
            space_run = 0
        elif char.isspace():
            if ascii_run:
                total += run_cost(ascii_run, ASCII_CHARS_PER_TOKEN)
                ascii_run = 0
            space_run += 1
        else:
            total += run_cost(ascii_run, ASCII_CHARS_PER_TOKEN) + run_cost(space_run, SPACE_CHARS_PER_TOKEN) + 1
            ascii_run = 0
            space_run = 0
        current = total + run_cost(ascii_run, ASCII_CHARS_PER_TOKEN) + run_cost(space_run, SPACE_CHARS_PER_TOKEN)
        if current > token_budget:
            break
        accepted = index - start + 1
        if char == "\n":
            last_newline = accepted
    if start + accepted < len(text) and last_newline:
        return last_newline
    return max(accepted, 1)


def _paginate_text(text: str, cursor: int, token_budget: int) -> tuple[str, int | None]:
    """Paginate by character cursor, preferring newline boundaries without data loss."""
    cursor = max(int(cursor or 0), 0)
    budget = max(int(token_budget or 0), 1)
    if cursor >= len(text):
        return "", None
    length = _prefix_length_for_budget(text, cursor, budget)
    content = text[cursor : cursor + length]
    next_cursor = cursor + length
    return content, next_cursor if next_cursor < len(text) else None


def _result(
    content: str,
    *,
    mode: str,
    next_cursor: int | None,
    token_budget: int,
    **extra: Any,
) -> dict[str, object]:
    return {
        "mode": mode,
        "content": content,
        "token_budget": token_budget,
        "estimated_tokens": estimate_tokens(content),
        "truncated": next_cursor is not None,
        "next_cursor": next_cursor,
        **extra,
    }


def _find_section(
    sections: list[dict[str, object]],
    requested: str,
    start_line: int | None = None,
) -> dict[str, object]:
    needle = str(requested or "").strip().casefold()
    if not needle:
        raise ValueError("section is required for section mode")
    exact = [item for item in sections if str(item.get("title") or "").casefold() == needle]
    matches = exact or [item for item in sections if needle in str(item.get("title") or "").casefold()]
    if not matches:
        raise ValueError(f"section not found: {requested}")
    if start_line is not None:
        positioned = [item for item in matches if int(item.get("start_line") or 0) == int(start_line)]
        if not positioned:
            raise ValueError(f"section {requested!r} was not found at line {start_line}")
        return positioned[0]
    if len(matches) > 1:
        lines = ", ".join(str(item.get("start_line")) for item in matches)
        raise ValueError(f"section {requested!r} is ambiguous; choose section_start_line from: {lines}")
    return matches[0]


def build_report_view(
    text: str,
    *,
    mode: str,
    query: str | None = None,
    section: str | None = None,
    section_start_line: int | None = None,
    cursor: int = 0,
    token_budget: int = DEFAULT_REPORT_QUERY_BUDGET,
    context_lines: int = 2,
) -> dict[str, object]:
    """Build a bounded outline, section, search, or full Markdown view."""
    mode = str(mode or "outline").strip().casefold()
    if mode not in {"outline", "meta", "section", "search", "full"}:
        raise ValueError("mode must be one of: outline, meta, section, search, full")
    token_budget = min(max(int(token_budget or 0), 1), MAX_REPORT_QUERY_BUDGET)
    context_lines = min(max(int(context_lines or 0), 0), MAX_REPORT_CONTEXT_LINES)
    source_text = str(text or "")

    if mode == "full":
        content, next_cursor = _paginate_text(source_text, cursor, token_budget)
        return _result(
            content,
            mode=mode,
            next_cursor=next_cursor,
            token_budget=token_budget,
            total_characters=len(source_text),
        )

    if mode == "search":
        needle = str(query or "").strip().casefold()
        if not needle:
            raise ValueError("query is required for search mode")
        lines = source_text.splitlines()
        matches = [index for index, line in enumerate(lines) if needle in line.casefold()]
        selected_indices: set[int] = set()
        for index in matches:
            selected_indices.update(range(max(index - context_lines, 0), min(index + context_lines + 1, len(lines))))
        search_text = "\n".join(f"L{index + 1}: {lines[index]}" for index in sorted(selected_indices))
        content, next_cursor = _paginate_text(search_text, cursor, token_budget)
        return _result(
            content,
            mode=mode,
            next_cursor=next_cursor,
            token_budget=token_budget,
            match_count=len(matches),
            returned_line_count=len(content.splitlines()) if content else 0,
        )

    sections = parse_markdown_sections(source_text)
    total_lines = len(source_text.splitlines())

    if mode in {"outline", "meta"}:
        outline_lines = [
            f"{'#' * int(item['level'])} {item['title']} (lines {item['start_line']}-{item['end_line']})"
            for item in sections
        ]
        outline_text = "\n".join(outline_lines)
        content, next_cursor = _paginate_text(outline_text, cursor, token_budget)
        return _result(
            content,
            mode=mode,
            next_cursor=next_cursor,
            token_budget=token_budget,
            # Titles/ranges already live in content. Avoid duplicating a large
            # outline in JSON responses and defeating the content budget.
            sections=[],
            total_sections=len(sections),
            total_lines=total_lines,
            outline_estimated_tokens=estimate_tokens(outline_text),
        )

    if mode == "section":
        lines = source_text.splitlines()
        selected = _find_section(sections, section or "", section_start_line)
        section_lines = lines[int(selected["start_line"]) - 1 : int(selected["end_line"])]
        content, next_cursor = _paginate_text("\n".join(section_lines), cursor, token_budget)
        return _result(
            content,
            mode=mode,
            next_cursor=next_cursor,
            token_budget=token_budget,
            section=selected,
            total_lines=len(section_lines),
        )

    raise AssertionError("unreachable report view mode")
