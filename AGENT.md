# Agent Notes

This project is an ARK DevKit / Unreal Blueprint clipboard-text analyzer, not a game implementation task.

## Local Control Center

The user-facing app is a local Vite + TypeScript web control center served by a Python standard-library backend:

- Frontend entry: `src/main.ts` and `src/styles.css`.
- Backend entry: `scripts/blueprint_tool_server.py`.
- Windows launcher: `scripts/launch_blueprint_tool.ps1`.
- Start it with `.\scripts\launch_blueprint_tool.ps1`; it builds the UI and opens `http://127.0.0.1:8765/`.
- The control center can capture one graph page from the Windows clipboard, run a pasted or DevKit-exported graph-page capture queue, rerun reports, inspect DevKit export quality, and run asset-level behavior compare.

Do not treat the old Phaser prototype files/assets as the product direction. The current product direction is the Blueprint analysis control center.

## Report Reading Order

When reviewing a captured Blueprint asset output directory, read these files first:

1. `next_actions.md` - the main action list for missing graph pages, defaults, components, and rerun commands.
2. `context_review.md` - the best triage for missing function notes, likely inherited/runtime defaults, and component context.
3. `notes_todo.md` - generated review queue for deciding which missing graph candidates should be added to `notes.md`.
4. `behavior_summary.md` - behavior-area overview grouped by ARK concerns such as Glide, Sliding, Nursing, MultiUse, Damage, Replication, Movement, Parachute, Status, Animation, and Orchestration; includes inferred behavior heuristics.
5. `capture_quality_report.md` - copy-completeness and likely missing Blueprint graph checks.
6. `diagnostics_report.md` - unresolved links, unknown sources, disconnected nodes, and confidence details.
7. `asset_report.md` - full audit report, useful after the first four files identify where to focus.
8. `graph_reports/<graph>_report.md` - only read the specific graph reports named by `next_actions.md` or `capture_quality_report.md`.

For asset compare output, read `behavior_impact_report.md` before the full `compare_report.md`. It groups changes by likely ARK behavior area and points to the graph/default/component evidence first.

## Files Usually Safe To Ignore

Do not read these by default unless debugging parser internals:

- `asset.json`
- `diagnostics.json`
- `capture_quality.json`
- `context_review.json` unless checking the control-center structured queue
- `call_graph.md`
- `graph_reports/*.json`
- `graph_reports/*_diagnostics.md`
- `ark_glossary.json`
- duplicate legacy `report.md`

The default asset report level should avoid writing large or redundant debug files. `context_review.json` is intentionally kept in standard output because the control center reads it instead of scraping Markdown. Use `--report-level debug` only when parser internals or regression tests need the full payload.

## Output Levels

- `--report-level compact`: writes only the main human reports.
- `--report-level standard`: default; writes the useful human reports, `asset_report.md`, `context_review.md`, structured `context_review.json`, `notes_todo.md`, `behavior_summary.md`, `call_graph_summary.md`, non-empty suggestions, and focused graph reports.
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
The web control center also has a `notes.md 判定` panel that writes `inherited:` or `ignore missing graph:` lines, reruns standard analysis, and filters already-noted functions from the queue.
