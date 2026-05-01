# Agent Notes

This project is an ARK DevKit / Unreal Blueprint clipboard-text analyzer, not a game implementation task.

## Report Reading Order

When reviewing a captured Blueprint asset output directory, read these files first:

1. `next_actions.md` - the main action list for missing graph pages, defaults, components, and rerun commands.
2. `notes_todo.md` - generated review queue for deciding which missing graph candidates should be added to `notes.md`.
3. `behavior_summary.md` - behavior-area overview grouped by ARK concerns such as Glide, Sliding, Nursing, MultiUse, Damage, and Replication; includes inferred behavior heuristics.
4. `capture_quality_report.md` - copy-completeness and likely missing Blueprint graph checks.
5. `diagnostics_report.md` - unresolved links, unknown sources, disconnected nodes, and confidence details.
6. `asset_report.md` - full audit report, useful after the first four files identify where to focus.
7. `graph_reports/<graph>_report.md` - only read the specific graph reports named by `next_actions.md` or `capture_quality_report.md`.

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
- `--report-level standard`: default; writes the useful human reports, `asset_report.md`, `notes_todo.md`, `behavior_summary.md`, `call_graph_summary.md`, non-empty suggestions, and focused graph reports.
- `--report-level debug`: writes full JSON payloads, complete call graph, glossary, and all per-graph debug artifacts.

Asset output generation cleans known stale generated files by default so old debug artifacts do not linger after a standard run. Use `--keep-stale-output` only when intentionally comparing generated artifacts by hand.

## Notes Sidecar

`notes.md` can suppress confirmed non-local functions from missing graph reports. Recognized examples:

```text
inherited: ClearJump, GetGlidingPitch
native: Delay, FormatAsTime
ignore missing graph: FooBar
SomeFunction: parent - implemented by Dino_Character_BP
```

When reviewing follow-up reports, treat noted functions as intentionally external unless new evidence shows they are implemented in the current asset.
Use `notes_todo.md` as the source of candidate lines to verify and then copy into `notes.md`.
