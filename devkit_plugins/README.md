# DevKit Plugins

Experimental ARK DevKit / Unreal Editor plugin sources live here.

The first plugin, `BlueprintToCodeExporter`, is a narrow graph-page queue
exporter. It exists because ARK DevKit Python can find known graphs by name but
does not expose a reliable API for enumerating every function/macro/event graph.
The C++ `UBlueprint::GetAllGraphs()` API does expose that list.

Keep plugins small and export data files into `captures/<BlueprintName>/`.
The Python analyzer and web control center remain the reporting layer.
