# ARK DevKit Blueprint Defaults Exporter

This folder contains Unreal Python scripts that run inside ARK DevKit / Unreal
Editor. They export Blueprint Class Defaults and component template defaults
into the sidecar files consumed by the local Blueprint translator.

## Recommended: Paste Path With GUI

Run this from Windows PowerShell:

```powershell
cd "<your Blueprint to Code folder>"
.\scripts\devkit_exporters\run_devkit_export_path_gui.ps1
```

Paste the Blueprint reference or Object Path copied from ARK DevKit, then click
`Save Path`. The GUI writes:

```text
<your Blueprint to Code folder>\captures\_devkit_export_request.json
```

It also copies the Python Console command to your clipboard.

Back in ARK DevKit, paste/run that command in Python Console mode. If you are
using the normal Output Log / command mode instead, use the `py exec(...)`
alternative shown by the GUI.

The exporter defaults to crash-safe component mode. It exports Blueprint
defaults and writes component candidates from `components_suggestions.json`;
it does not recursively reflect live Unreal component objects because that can
crash some ARK DevKit Python builds.

If PowerShell blocks local scripts, run:

```powershell
powershell -ExecutionPolicy Bypass -File ".\scripts\devkit_exporters\run_devkit_export_path_gui.ps1"
```

Fallback without the launcher:

```powershell
python scripts\devkit_exporters\devkit_export_path_gui.py
```

## Export Current Blueprint Defaults Directly

1. Open the Blueprint in ARK DevKit, or select it in the Content Browser.
2. Run this in the DevKit Python Console:

```python
BLUEPRINT_TO_CODE_PROJECT_ROOT = r"<your Blueprint to Code folder>"; exec(open(r"<your Blueprint to Code folder>\scripts\devkit_exporters\export_current_blueprint_defaults.py", encoding="utf-8").read())
```

If you are in the normal Output Log / command mode, prefix the Python code with
`py`:

```text
py BLUEPRINT_TO_CODE_PROJECT_ROOT = r"<your Blueprint to Code folder>"; exec(open(r"<your Blueprint to Code folder>\scripts\devkit_exporters\export_current_blueprint_defaults.py", encoding="utf-8").read())
```

3. The exporter writes files under:

```text
<your Blueprint to Code folder>\captures\<BlueprintName>\
  defaults.json
  components.json
  devkit_export_report.md
  devkit_export_log.json
```

4. Back in Windows PowerShell, rerun the asset analyzer:

```powershell
cd "<your Blueprint to Code folder>"
python scripts\bp_clipboard_to_prompt.py --asset-dir captures\<BlueprintName>
```

If current Blueprint detection fails, the exporter now tries these fallbacks:

1. `captures/_devkit_export_request.json` written by the GUI above.
2. A Blueprint path already copied to the clipboard.
3. A small Tkinter paste window inside DevKit, if that Python build includes Tkinter.

## If All Automatic Detection Fails

Edit `export_current_blueprint_defaults.py` and set `ASSET_PATH` near the top:

```python
ASSET_PATH = "/Game/Mods/MyMod/MilkGlider_Character_BP.MilkGlider_Character_BP"
```

Then run the same `exec(open(...).read())` command again in Python Console mode.

## Output Contract

`defaults.json` uses:

```json
{
  "schema": "blueprint-translator.defaults.v1",
  "variables": {
    "SomeBlueprintVariable": {
      "default": true,
      "type": "bool",
      "source": "blueprint_variable"
    }
  },
  "classDefaults": {
    "bReplicates": {
      "default": true,
      "type": "bool",
      "source": "class_default"
    }
  }
}
```

`components.json` uses:

```json
{
  "schema": "blueprint-translator.components.v1",
  "components": [
    {
      "name": "CharacterMovement",
      "class": "CharacterMovementComponent",
      "defaults": {
        "MaxWalkSpeed": {
          "default": 600.0,
          "type": "float"
        }
      }
    }
  ]
}
```

The local analyzer already accepts these sidecar formats.

## Export The DevKit UClass Hierarchy

First build the class seed in normal PowerShell from the repository root:

```powershell
python .\scripts\devkit_exporters\build_kb_class_hierarchy_seed.py
```

The builder opens the published
`knowledge_base\discovery_bundle\kb_discovery.sqlite` database read-only and
writes a deterministic seed to
`knowledge_base\devkit_class_hierarchy\class_hierarchy_seed.json`. It covers
class paths observed on the asset, class-edge, interface, component,
function-owner, and default-owner surfaces. Short component names must resolve
uniquely. If one does not, the builder fails before publishing the seed; supply
an explicit mapping and rerun:

```powershell
python .\scripts\devkit_exporters\build_kb_class_hierarchy_seed.py --short-class MysteryComponent=/Script/Module.MysteryComponent
```

After a successful build, the script prints a ready-to-paste ARK DevKit Python
Console command. Run that command after the Asset Registry is ready. It wires
`BTC_KB_CLASS_HIERARCHY_SEED_FILE` to the generated seed before executing:

```python
import os; os.environ["BTC_KB_CLASS_HIERARCHY_SEED_FILE"] = r"<your Blueprint to Code folder>\knowledge_base\devkit_class_hierarchy\class_hierarchy_seed.json"; BLUEPRINT_TO_CODE_PROJECT_ROOT = r"<your Blueprint to Code folder>"; exec(open(r"<your Blueprint to Code folder>\scripts\devkit_exporters\export_kb_class_hierarchy_snapshot.py", encoding="utf-8").read())
```

It resolves class relationships from the Asset Registry's
`TopLevelAssetPath` ancestry API and explicit Blueprint relationship tags.
Undocumented `unreal.Class` methods are only retained as partial evidence:
missing interface APIs never turn an unknown interface set into a confirmed
empty set. Output is checkpointed and published as an immutable generation
under:

```text
knowledge_base\devkit_class_hierarchy\
  class_hierarchy_manifest.json
  generations\<generation-id>\
    class_hierarchy.jsonl
    class_hierarchy_checkpoint.json
```

Set `BTC_KB_CLASS_HIERARCHY_OUTPUT` to use another working directory, or
`BTC_KB_CLASS_HIERARCHY_BATCH_SIZE` to change the default 250-class progress-log
group. Durability is still per class: the active marker is written before live
reflection and the checkpoint is advanced after each committed row. The seed's
content SHA participates in the generation signature. Set
`BTC_KB_DEVKIT_BUILD_ID` when a DevKit package changes without changing the
reported engine version.

Rows expose separate `parent_status` and `interfaces_status` fields. Unknown
relationships remain `PARTIAL` or `NOT_RECOVERED`; the exporter never guesses a
native parent from a class name. A hard interruption leaves an active-class
marker. The first interruption retries the same class and generation. Only a
second consecutive interruption of that same class and generation emits
`QUARANTINED_AFTER_REPEATED_INTERRUPTION` with reason
`REPEATED_INTERRUPTION_SAME_CLASS_GENERATION`; confirmed Registry parent and
interface evidence is retained on that row before the exporter advances to the
next class. Prepared generations survive a Windows manifest sharing violation
and are verified and republished on the next run.

## Graph Name Candidate Validation

The local control center can mine likely graph page names from the Blueprint
`.uasset` before you run this DevKit exporter. It writes:

```text
captures\<BlueprintName>\graph_candidates_uasset.json
```

When this exporter runs inside ARK DevKit, it reads that file and validates each
candidate with:

```python
unreal.BlueprintEditorLibrary.find_graph(blueprint, name)
```

Accepted graph pages are added to `graph_queue.txt`; rejected candidates are
written to `graph_candidates_rejected.json`. This avoids manually typing every
page name while still letting ARK DevKit confirm which strings are real graph
pages.
