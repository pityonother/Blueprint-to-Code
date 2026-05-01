# Petal Walk

`Petal Walk` is a Phaser 3 + TypeScript + Vite narrative walking prototype for the browser.

## Run

```bash
npm install
npm run dev
```

Open the local Vite URL in a browser.

## Controls

- `Right Arrow` / `D`: walk right
- `Space` / `E`: advance text
- `Esc`: toggle pause overlay
- Touch right half: walk right

## Notes

- v0.1 uses generated placeholder art.
- Audio is intentionally stubbed for the first prototype pass.

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
For large real assets, start with `capture_quality_report.md`; it separates likely missing Blueprint graph pages from native/Kismet/inherited call noise and lists the defaults/components worth filling first.
Asset reports also write `next_actions.md`, `defaults_suggestions.json`, and `components_suggestions.json` so you can fill Class Defaults and component context without digging through the full report by hand.

Asset report output is tiered:

- `--report-level compact`: write only the main human reports.
- `--report-level standard`: default; write `next_actions.md`, `behavior_summary.md`, `capture_quality_report.md`, `diagnostics_report.md`, `asset_report.md`, `call_graph_summary.md`, non-empty suggestions, and focused graph reports.
- `--report-level debug`: write full parser payloads such as `asset.json`, `call_graph.md`, `capture_quality.json`, `diagnostics.json`, and all per-graph JSON/diagnostic files.

Known generated asset outputs are cleaned before each asset report run, so stale debug files do not remain after returning to standard output. Pass `--keep-stale-output` only when intentionally comparing old generated files.

Export Blueprint Class Defaults and component defaults from ARK DevKit:

```powershell
.\scripts\devkit_exporters\run_devkit_export_path_gui.ps1
```

Paste the Blueprint Object Path/reference into the GUI, click `Save Path`, then run the copied command in ARK DevKit's Python Console mode. The DevKit-side command is:

```python
exec(open(r"C:\Users\ac\Documents\project gaming\Blueprint to Code\scripts\devkit_exporters\export_current_blueprint_defaults.py", encoding="utf-8").read())
```

If you are in normal Output Log / command mode instead of Python Console mode, use:

```text
py exec(open(r"C:\Users\ac\Documents\project gaming\Blueprint to Code\scripts\devkit_exporters\export_current_blueprint_defaults.py", encoding="utf-8").read())
```

The exporter also still tries the currently opened/selected Blueprint, the saved GUI request, clipboard text, and an in-DevKit paste dialog when available. It writes `defaults.json`, `components.json`, `devkit_export_report.md`, and `devkit_export_log.json` under `captures/<BlueprintName>/`. Then rerun the asset analyzer:

Component export runs in crash-safe mode by default: it writes analysis candidates rather than recursively reflecting live Unreal component objects, which can crash some ARK DevKit Python builds.
Crash-safe mode now also attempts a shallow SimpleConstructionScript/component-template scan for component names, classes, and paths; it still avoids recursive component default reflection.

```powershell
python scripts\bp_clipboard_to_prompt.py --asset-dir captures\<BlueprintName>
```

For a single non-interactive capture from an existing text file:

```powershell
python scripts\bp_clipboard_to_prompt.py --capture-asset captures\Achatina_Character_BP --capture-once EventGraph --input tests\fixtures\real_ark_achatina_beginplay.txt --capture-no-report
```

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
