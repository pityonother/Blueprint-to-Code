# ARK DevKit Blueprint Defaults Exporter

This folder contains Unreal Python scripts that run inside ARK DevKit / Unreal
Editor. They export Blueprint Class Defaults and component template defaults
into the sidecar files consumed by the local Blueprint translator.

## Recommended: Paste Path With GUI

Run this from Windows PowerShell:

```powershell
cd "C:\Users\ac\Documents\project gaming\Blueprint to Code"
.\scripts\devkit_exporters\run_devkit_export_path_gui.ps1
```

Paste the Blueprint reference or Object Path copied from ARK DevKit, then click
`Save Path`. The GUI writes:

```text
C:\Users\ac\Documents\project gaming\Blueprint to Code\captures\_devkit_export_request.json
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
exec(open(r"C:\Users\ac\Documents\project gaming\Blueprint to Code\scripts\devkit_exporters\export_current_blueprint_defaults.py", encoding="utf-8").read())
```

If you are in the normal Output Log / command mode, prefix the Python code with
`py`:

```text
py exec(open(r"C:\Users\ac\Documents\project gaming\Blueprint to Code\scripts\devkit_exporters\export_current_blueprint_defaults.py", encoding="utf-8").read())
```

3. The exporter writes files under:

```text
C:\Users\ac\Documents\project gaming\Blueprint to Code\captures\<BlueprintName>\
  defaults.json
  components.json
  devkit_export_report.md
  devkit_export_log.json
```

4. Back in Windows PowerShell, rerun the asset analyzer:

```powershell
cd "C:\Users\ac\Documents\project gaming\Blueprint to Code"
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
