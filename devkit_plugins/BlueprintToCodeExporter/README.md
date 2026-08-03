# Blueprint To Code Exporter

Editor-only Unreal/ARK DevKit plugin for exporting Blueprint graph page queues
into the local Blueprint to Code analyzer.

This is intentionally small: it does not try to decompile Blueprint behavior.
Its first job is to prove that ARK DevKit can load a C++ editor plugin and to
write the real graph page list using `UBlueprint::GetAllGraphs()`.

## What It Exports

When one or more Blueprint assets are selected in the Content Browser, use:

```text
Tools -> Blueprint to Code -> Export Selected Blueprint Graph Queue
```

The plugin writes these files under:

```text
<your Blueprint to Code folder>\captures\<BlueprintName>\
```

- `graph_queue.txt` - queue format already understood by the web control center.
- `graph_pages_cpp.json` - structured graph metadata.
- `cpp_export_report.md` - human-readable export report.

The exporter resolves its project/output root in this order:

1. `BLUEPRINT_TO_CODE_ROOT` when explicitly configured;
2. an ancestor of the plugin base containing
   `scripts/bp_clipboard_to_prompt.py`;
3. the current user's `Documents/Blueprint to Code` folder.

All three paths are normalized with Unreal's platform path APIs, so no
machine-specific user directory or path separator is compiled into the plugin.
Set `BLUEPRINT_TO_CODE_ROOT` when your local repository is somewhere else:

```powershell
[Environment]::SetEnvironmentVariable(
  "BLUEPRINT_TO_CODE_ROOT",
  "<your Blueprint to Code folder>",
  "User"
)
```

Restart ARK DevKit after changing the environment variable.

## Install/Verification Notes

This folder is a source plugin. Copy or symlink `BlueprintToCodeExporter` into
an ARK DevKit `Plugins` folder that supports editor plugins, then rebuild or
let the DevKit prompt compile it if that workflow is available.

From the repository root, the helper script can copy the plugin and set the
output-root environment variable. It also refuses to install into ARK DevKit
builds that can scan plugins but cannot compile C++ source plugins, because
those builds will fail on startup with `cannot find module BlueprintToCodeExporter`.

```powershell
.\scripts\devkit_plugins\install_blueprint_to_code_exporter.ps1
```

First success criteria:

1. ARK DevKit starts without disabling the plugin.
2. The Tools menu shows `Blueprint to Code`.
3. Selecting a Blueprint asset and running the menu command writes
   `graph_queue.txt`.
4. The web control center can load that queue.

If ARK DevKit cannot compile/load custom editor C++ plugins, stop here and use
the lower-risk fallback: paste candidate graph names into the control center and
validate them with the DevKit Python exporter.
