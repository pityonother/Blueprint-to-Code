# Blueprint to Code

ARK DevKit / Unreal Blueprint clipboard-text analyzer for turning copied Blueprint
graph pages, exported Class Defaults, and component context into reports that are
useful for mod behavior review.

## Control Center

The easiest entrypoint is the local web control center. It uses the existing Vite
frontend stack for the interface and a small Python standard-library backend for
running the analyzer, opening reports, and preparing DevKit export requests.

Run from Windows PowerShell:

```powershell
.\scripts\launch_blueprint_tool.ps1
```

The launcher builds the UI, starts `scripts/blueprint_tool_server.py`, and opens
`http://127.0.0.1:8765/`. From the control center you can:

- select an asset under `captures/`
- regenerate standard, compact, or debug reports
- capture one Blueprint graph page from the Windows clipboard into `graphs/*.txt`
- paste or load a DevKit-exported graph-page queue and save copied pages one by one without retyping each page name; the queue can be loaded as compact, supplemental-context, or full graph set
- confirm before replacing an existing graph page; old copies are backed up under `graphs/_backups/`
- open `next_actions.md`, `context_review.md`, `notes_todo.md`, `behavior_summary.md`, and other key reports
- review missing function candidates and append confirmed parent/native or ignored functions directly to `notes.md`; the app reruns standard analysis afterward so previews stay fresh
- open the asset output folder or focused `graph_reports/`
- paste a DevKit Blueprint Object Path and copy the exporter command
- check DevKit default/component export health, warnings, skipped properties, and SCS/component-template candidates
- compare two captured assets and generate `behavior_impact_report.md`
- run long analyses/asset compares as background jobs with visible status and cancellation

For frontend-only development:

```powershell
npm install
npm run dev
```

For the combined local app without the PowerShell launcher:

```powershell
npm run control
```

## ARK DevKit Blueprint Translator

The Blueprint translator entrypoint lives in `scripts/bp_clipboard_to_prompt.py`.
The implementation is split under `scripts/blueprint_translator/`:

- `cli.py`, `translate.py`, `asset.py`, `compare.py`: command parsing and workflows.
- `core.py`, `parser.py`, `flow.py`: Blueprint text parsing, payload building, execution/data flow.
- `renderers.py`, `diagnostics.py`, `output.py`: reports, pseudocode, diagnostics, and file output.
- `config.py`, `patterns.py`, `models.py`, `context.py`, `utils.py`: shared semantics, regexes, data models, sidecar parsing, and helpers.

The `tests/` directory must stay beside `scripts/` at the project root because the tests import the script by path.

Run from copied Blueprint nodes:

```bash
scripts/run_bp_translator.bat
```

Capture a multi-page Blueprint asset from the clipboard:

```powershell
python scripts\bp_clipboard_to_prompt.py --capture-asset Achatina_Character_BP
```

The capture wizard creates `captures/Achatina_Character_BP/graphs/`, saves each copied graph page as a `.txt` file, writes `manifest.json`, creates starter `defaults.json`, `components.json`, and `notes.md`, then runs the asset report into `captures/Achatina_Character_BP/output/`.
If a graph page already exists, the CLI refuses to overwrite it unless you pass `--capture-overwrite`; overwritten files are copied to `graphs/_backups/` first. The web control center asks for confirmation before sending the overwrite request.
For large real assets, start with `capture_quality_report.md`; it separates likely missing Blueprint graph pages from native/Kismet/inherited call noise and lists the defaults/components worth filling first.
Asset reports also write `next_actions.md`, `context_review.md`, `context_review.json`, `defaults_suggestions.json`, and `components_suggestions.json` so you can fill Class Defaults and component context without digging through the full report by hand. `context_review.md` is the best place to separate likely runtime state, likely inherited/parent state, and true manual default checks; `context_review.json` is the structured version used by the control center.

Asset report output is tiered:

- `--report-level compact`: write only the main human reports.
- `--report-level standard`: default; write `next_actions.md`, `context_review.md`, `context_review.json`, `notes_todo.md`, `behavior_summary.md`, `capture_quality_report.md`, `diagnostics_report.md`, `asset_report.md`, `call_graph_summary.md`, non-empty suggestions, and focused graph reports.
- `--report-level debug`: write full parser payloads such as `asset.json`, `call_graph.md`, `capture_quality.json`, `context_review.json`, `diagnostics.json`, and all per-graph JSON/diagnostic files.

`behavior_summary.md` includes ARK-focused rule checks for Glide, Sliding, Nursing, MultiUse, Replication, Damage, Movement, Parachute, HUD, Passenger, Status, Animation, Orchestration, and CollapsedGraph groups. These checks are still heuristic, but they are designed to point you at the defaults, components, and graph pages that matter first.
The behavior summary rules live in `scripts/blueprint_translator/behavior_report.py`, so new ARK behavior areas can be added and tested without expanding the core asset orchestration module.

Known generated asset outputs are cleaned before each asset report run, so stale debug files do not remain after returning to standard output. Pass `--keep-stale-output` only when intentionally comparing old generated files.

Use `notes.md` to suppress known non-local function candidates after you verify them in ARK DevKit:

```text
inherited: ClearJump, GetGlidingPitch
native: Delay, FormatAsTime
ignore missing graph: FooBar
SomeFunction: parent - implemented by Dino_Character_BP
```

The analyzer also writes `notes_todo.md`, `context_review.md`, and `context_review.json`, which turn remaining likely-missing function graph calls into review templates for `notes.md`. In the web control center, use the `notes.md 判定` panel to select confirmed parent/native functions and write them without hand-editing the file; after saving notes, the app automatically runs a standard analysis to refresh the reports and queue.

Export Blueprint Class Defaults and component defaults from ARK DevKit:

```powershell
.\scripts\devkit_exporters\run_devkit_export_path_gui.ps1
```

Paste the Blueprint Object Path/reference into the GUI, click `Save Path`, then run the copied command in ARK DevKit's Python Console mode. The DevKit-side command is:

ARK DevKit's Python Console is global, not tied to the currently visible graph tab. The exporter therefore prioritizes the Object Path saved by the control center request file. If you do not use the GUI request, select the target Blueprint asset in the Content Browser before running the command; the active graph page alone is not a reliable asset selector.

```python
exec(open(r"C:\Users\ac\Documents\project gaming\Blueprint to Code\scripts\devkit_exporters\export_current_blueprint_defaults.py", encoding="utf-8").read())
```

If you are in normal Output Log / command mode instead of Python Console mode, use:

```text
py exec(open(r"C:\Users\ac\Documents\project gaming\Blueprint to Code\scripts\devkit_exporters\export_current_blueprint_defaults.py", encoding="utf-8").read())
```

The exporter also still tries the currently opened/selected Blueprint, the saved GUI request, clipboard text, and an in-DevKit paste dialog when available. It writes `defaults.json`, `components.json`, `graph_pages.json`, `graph_queue.txt`, `graph_discovery_debug.json`, `graph_discovery_report.md`, `devkit_export_report.md`, and `devkit_export_log.json` under `captures/<BlueprintName>/`. The control center can load `graph_queue.txt` directly into the batch capture queue. If `graph_queue.txt` is empty, inspect `graph_discovery_report.md` and `graph_discovery_debug.json` to see what graph-related fields this ARK DevKit Python build exposes. Then rerun the asset analyzer:

Component export runs in crash-safe mode by default: it writes analysis candidates rather than recursively reflecting live Unreal component objects, which can crash some ARK DevKit Python builds.
Crash-safe mode now also attempts a shallow SimpleConstructionScript/component-template scan for component names, classes, and paths; it still avoids recursive component default reflection.

### UAsset Graph Name Candidates

For large Blueprints, use the control center DevKit panel before hand-typing
graph page names:

1. Paste the Blueprint Object Path.
2. Click `从 .uasset 提取分页候选名`.
3. Click `保存候选名并复制 DevKit 验证命令`.
4. Run the copied command inside ARK DevKit.
5. Load the generated `graph_queue.txt` in the capture queue. Start with `载入精简采集`, use `载入补充上下文` only if the report still says context is missing, and reserve `载入全部` for deep debugging.

The local extractor maps `/Game/.../MyBP.MyBP` to the installed ARK DevKit
`Projects/ShooterGame/Content/.../MyBP.uasset`, scans safe ASCII/UTF-16 strings,
and writes:

```text
captures/<BlueprintName>/graph_candidates_uasset.json
captures/<BlueprintName>/graph_candidates_uasset.txt
captures/<BlueprintName>/graph_candidates_uasset_report.md
```

The DevKit exporter then validates those candidates with
`BlueprintEditorLibrary.find_graph(blueprint, name)`. Validated pages are written
to `graph_queue.txt`; rejected names are written to
`graph_candidates_rejected.json`.

`graph_queue.txt` can include more than the top visible editor tabs: event graphs,
functions, Blueprint overrides, RPC/replication graphs, macros, and collapsed
graphs may all be valid Unreal graph objects. The control center classifies them
into automatic tiers: `精简采集` loads the recommended queue, `补充上下文` adds
supporting context graphs, and `全部` is only for deep debugging. The normal
workflow should not depend on screenshot-confirmed name lists.

Command-line equivalent:

```powershell
python scripts\uasset_graph_candidates.py "/Game/Genesis2/Dinos/LionfishLion/LionfishLion_Character_BP.LionfishLion_Character_BP"
```

### Experimental UAsset Graph Content Reader

The control center can now go past graph names and read recoverable graph content
directly from `.uasset` / `.uexp` exports:

1. Paste the Blueprint Object Path.
2. Click `从 .uasset 读取图内容`.
3. Review `uasset_graph_read_report.md` and the generated standard asset reports.
4. If some pages are partial, click `只补采失败图页` to load a focused manual copy queue.

The reader locates ExportMap serialized data, reads `EdGraph.Nodes`, extracts
K2 node classes, node coordinates, function/variable/event references, and
recoverable custom pin/link data with per-graph confidence and failure
categories. It writes:

```text
captures/<BlueprintName>/uasset_package.json
captures/<BlueprintName>/uasset_exports.json
captures/<BlueprintName>/uasset_properties.json
captures/<BlueprintName>/uasset_unknown_properties.json
captures/<BlueprintName>/uasset_property_parse_report.md
captures/<BlueprintName>/uasset_pin_links.json
captures/<BlueprintName>/uasset_link_resolution_report.md
captures/<BlueprintName>/uasset_partial_graph_triage.json
captures/<BlueprintName>/uasset_partial_graph_triage.md
captures/<BlueprintName>/uasset_quality_gates.json
captures/<BlueprintName>/uasset_quality_gates.md
captures/<BlueprintName>/uasset_graph_nodes.json
captures/<BlueprintName>/uasset_graph_read_report.md
captures/<BlueprintName>/uasset_failed_graph_queue.txt
captures/<BlueprintName>/uasset_failed_graph_queue.json
captures/<BlueprintName>/uasset_compare_matrix.json
captures/<BlueprintName>/uasset_vs_clipboard_compare.md
captures/<BlueprintName>/graphs_from_uasset/<GraphName>.json
```

`graphs_from_uasset/*.json` is a parser-compatible graph payload. The asset
analyzer prefers copied `graphs/*.txt` when present, and falls back to these
binary graph payloads when no clipboard graph pages exist. When both clipboard
text and binary graph payloads exist, the analyzer writes a validation matrix
comparing node class distribution, function/variable/event recovery, pin counts,
and link counts.

The binary reader deliberately reports four useful kinds of uncertainty:

- `complete`: nodes, pins, and node-level links were recovered well enough for
  normal reports.
- `partial`: the graph is usable, but pin coverage, link coverage, or custom
  data layout needs more rules.
- `heuristic`: the graph was recovered mainly through byte-pattern scanning.
- `needs_clipboard` / `failed`: manually copy the page or add a new binary rule.

`uasset_link_resolution_report.md` separates exact target pin matches from
`resolved_pin_heuristic`, where the reader resolves the target node and chooses
the most likely target pin by exec/data direction and pin category. Treat
heuristic links as graph-level useful but pin-level low confidence until a
clipboard comparison or stronger LinkedTo layout rule confirms them.

Command-line equivalent:

```powershell
python scripts\bp_clipboard_to_prompt.py --asset-binary "/Game/Genesis2/Dinos/LionfishLion/LionfishLion_Character_BP.LionfishLion_Character_BP"
```

Use `--asset-binary-no-report` to only write extraction artifacts, and
`--uasset-max-graphs 3` for a fast smoke test.

### Experimental C++ Graph Queue Exporter

ARK DevKit Python can validate known graph names, but it has not exposed a reliable
API for enumerating every function/macro/event graph. The experimental editor
plugin under `devkit_plugins/BlueprintToCodeExporter/` uses Unreal's C++
`UBlueprint::GetAllGraphs()` API to export the real graph-page queue.

On installed ARK DevKit builds that lack `Engine\Source\Runtime` and
`Engine\Intermediate\Build\BuildRules\UE5Rules.dll`, this source plugin cannot
compile. Prefer the UAsset candidate workflow above unless you have a separate
plugin build environment or a precompiled DLL.

Install helper:

```powershell
.\scripts\devkit_plugins\install_blueprint_to_code_exporter.ps1
```

When prompted, paste an ARK DevKit `Plugins` directory that can load editor
plugins. The script first checks whether that DevKit build appears able to
compile C++ source plugins; if it only has installed binaries and no usable
engine rules/source, it aborts instead of installing a plugin that will fail at
startup with `cannot find module BlueprintToCodeExporter`. If the check passes,
the script copies the plugin and sets `BLUEPRINT_TO_CODE_ROOT` to this
repository. Restart ARK DevKit afterward. First verification target:

```text
Tools -> Blueprint to Code -> Export Selected Blueprint Graph Queue
```

Select a Blueprint asset in the Content Browser before running the menu command.
On success the plugin writes `graph_queue.txt`, `graph_pages_cpp.json`, and
`cpp_export_report.md` under `captures/<BlueprintName>/`. Load the queue from the
control center and continue copying graph pages in order.

If ARK DevKit cannot compile or load custom C++ editor plugins, stop using this
path and fall back to the Python exporter plus manual/candidate graph names.
To remove a failed test install:

```powershell
.\scripts\devkit_plugins\install_blueprint_to_code_exporter.ps1 -DevKitPluginsDir "C:\Program Files\Epic Games\ARKDevkit\Engine\Plugins" -Uninstall
```

```powershell
python scripts\bp_clipboard_to_prompt.py --asset-dir captures\<BlueprintName>
```

For a single non-interactive capture from an existing text file:

```powershell
python scripts\bp_clipboard_to_prompt.py --capture-asset captures\Achatina_Character_BP --capture-once EventGraph --input tests\fixtures\real_ark_achatina_beginplay.txt --capture-no-report
```

Compare two captured Blueprint assets:

```powershell
python scripts\bp_clipboard_to_prompt.py --compare-asset captures\OldAsset_BP captures\NewAsset_BP --output-dir captures\_compare_reports\old_to_new
```

Asset compare writes `compare_report.md`, `compare_summary.md`, `compare.json`, and `behavior_impact_report.md`. The behavior impact report groups changes by likely ARK behavior areas such as Parachute, Glide, Sliding, Nursing, MultiUse, Damage, Passenger, Movement, HUD, and Replication.

Run tests:

```powershell
$files = @("scripts\bp_clipboard_to_prompt.py") + (Get-ChildItem scripts\blueprint_translator -Filter *.py | ForEach-Object FullName)
python -m py_compile @files
python -m unittest discover -s tests -v
```

Current notes:

- `--provider` is reserved for future integration. The default is `none`, and the script only generates prompts; it does not call Ollama, LM Studio, OpenAI, or Anthropic yet.
- `pseudocode.md` and `cpp_reference.md` are for understanding Blueprint logic. They are not guaranteed to compile or exactly match Unreal generated code.
- Unreal Blueprint `Ctrl+C` text does not include full Class Defaults, Components, inherited defaults, parent class behavior, or native C++ function bodies.
- Use sidecar context files when those details matter: `--defaults-file`, `--components-file`, `--notes-file`, `--parent-class`, `--interfaces`, and `--tags`.
- `--make-context-template` creates a starter context template for Asset name, Parent class, Components, Class Defaults, Replication, Inventory, Stasis, Octree, Radius, Range, Food, Buff, MultiUse, and test observations.
