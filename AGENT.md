# Agent Notes

This project is an ARK DevKit / Unreal Blueprint clipboard-text analyzer, not a game implementation task.

## Report Reading Order

When reviewing a captured Blueprint asset output directory, read these files first:

1. `next_actions.md` - the main action list for missing graph pages, defaults, components, and rerun commands.
2. `capture_quality_report.md` - copy-completeness and likely missing Blueprint graph checks.
3. `diagnostics_report.md` - unresolved links, unknown sources, disconnected nodes, and confidence details.
4. `asset_report.md` - full audit report, useful after the first three files identify where to focus.
5. `graph_reports/<graph>_report.md` - only read the specific graph reports named by `next_actions.md` or `capture_quality_report.md`.

## Files Usually Safe To Ignore

Do not read these by default unless debugging parser internals:

- `asset.json`
- `diagnostics.json`
- `capture_quality.json`
- `call_graph.md`
- `graph_reports/*.json`
- `graph_reports/*_diagnostics.md`
- `ark_glossary.json`
- duplicate legacy `report.md`

The default asset report level should avoid writing those large or redundant files. Use `--report-level debug` only when parser internals or regression tests need the full payload.

## Output Levels

- `--report-level compact`: writes only the main human reports.
- `--report-level standard`: default; writes the useful human reports, `asset_report.md`, `call_graph_summary.md`, non-empty suggestions, and focused graph reports.
- `--report-level debug`: writes full JSON payloads, complete call graph, glossary, and all per-graph debug artifacts.

Asset output generation cleans known stale generated files by default so old debug artifacts do not linger after a standard run. Use `--keep-stale-output` only when intentionally comparing generated artifacts by hand.
