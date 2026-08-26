# Blueprint To Code Exporter

Editor-only Unreal/ARK DevKit plugin for exporting Blueprint graph page queues
and publishing a bounded, read-only Editor state snapshot to the local
Blueprint to Code analyzer.

This is intentionally small: it does not try to decompile Blueprint behavior.
Its first job is to prove that ARK DevKit can load a C++ editor plugin and to
write the real graph page list using `UBlueprint::GetAllGraphs()`. Version
`0.2.0` also reads public Editor state; it never creates, connects, compiles,
saves, or otherwise modifies Blueprint content.

## Read-only Editor State Bridge

The plugin checks public Editor state every 250 ms and writes only when the
semantic state changes or a two-second heartbeat is due. It atomically replaces:

```text
<your Blueprint to Code folder>\.arkdev-bridge\editor_state.json
```

The snapshot is capped at 2,000 focused-graph nodes and contains the active
Blueprint object path, focused graph, NodeGuid/graph-space position, package
dirty state, compile status, build identity, sequence, and UTC timestamp. The
current public UE 5.5 interface available for this implementation does not
provide a stable selection contract through `IBlueprintEditor`, so the plugin
reports `selectionStatus=UNSUPPORTED_BY_DEVKIT_BUILD` and does not advertise
`READ_SELECTION`.

`graphStatus` distinguishes an unavailable public Blueprint editor interface from
an available editor with no focused graph. Snapshot publication writes a temporary
file beside the destination and atomically replaces it on Windows.

Diagnostic menu entries are available under:

```text
Tools -> Blueprint to Code -> Show Editor Bridge Status
Tools -> Blueprint to Code -> Write Editor State Snapshot Now
```

Normal heartbeat operation is automatic and does not require either menu item.
On normal plugin shutdown the snapshot is deleted. A crash-left snapshot is
rejected by the MCP reader after the six-second freshness window.

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
5. `.arkdev-bridge\editor_state.json` updates within two seconds while a
   Blueprint is open.
6. `python scripts\validate_arkdev_editor_bridge_snapshot.py` reports a fresh,
   connected snapshot without printing its machine-local path.

If ARK DevKit cannot compile/load custom editor C++ plugins, stop here and use
the lower-risk fallback: paste candidate graph names into the control center and
validate them with the DevKit Python exporter.

The complete runtime checklist is in
`docs/mcp/EDITOR_BRIDGE_MANUAL_ACCEPTANCE.md`. A fixture or source-contract test
does not count as a real ARK DevKit runtime pass.
