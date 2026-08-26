# Evidence-Guided Exact Node Binding Probe

## Current conclusion

Status on 2026-08-12: `BLOCKED_BEFORE_MANUAL_RUN`.

The repository can define a bounded, read-only lookup route for known Blueprint
nodes, but the current local Evidence cannot authorize a live request. The real
builder preflight stops first with `EVIDENCE_NOT_AUTHORITATIVE`: the available
v2 compatibility generation cannot satisfy the existing current-v3 Evidence
and Interpretation authority boundary. A separate bounded read-only audit also
found that every inspected node lacks a non-zero 32-hex `NodeGuid`; names are
only locators. If authority were restored without GUIDs, the builder would stop
with `NODE_GUID_NOT_AVAILABLE`. No ARK DevKit run is requested.

This is an Evidence gap, not proof that the live Blueprint nodes do not exist.

## Scope and safety boundary

The probe is deliberately narrower than a Graph Snapshot or Patch Executor:

- one explicit Blueprint asset and one explicit graph;
- 1 through 12 existing Evidence node refs;
- `find_object(graph, objectName, EdGraphNode)`, then `load_object` only when
  the find result is `None`;
- exact direct outer (or an `EdGraph` with the same non-empty full path), exact
  runtime class name, and exact `NodeGuid` before a node is `EXACT`;
- read-only position and Pin inspection, capped at 64 Pins per node and 512 per
  request;
- no `ObjectIterator`, global name search, object creation, Blueprint mutation,
  compile, save, editor opening, connection changes, MCP tool registration, or
  Patch execution.

`SIGNATURE_ONLY` means that live Pins agree with Evidence by
`name + direction + ordinal`. It does not bind a live Pin UObject to an
Evidence `pinRef` and does not make mutation safe.
Accordingly, Phase 3C never emits `EXACT_NODE_AND_PIN_BINDING`; even a complete
matching signature routes to `EXACT_NODE_BINDING_PIN_PARTIAL`. The exact-Pin
route remains reserved for a separately verified live Pin identity adapter.

## Unreal 5.5 API boundary

The implementation is constrained to Epic's Unreal Python 5.5 documentation:

- [`unreal.find_object` and `unreal.load_object`](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/module/unreal?application_version=5.5)
- [`unreal.EdGraphNode`](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/EdGraphNode?application_version=5.5)
- [`unreal.K2Node`](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/K2Node?application_version=5.5)
- [`get_class`, `get_outer`, `get_path_name`, and `get_editor_property`](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/_ObjectBase?application_version=5.5)
- [`BlueprintEditorLibrary.find_graph/find_event_graph`](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/BlueprintEditorLibrary?application_version=5.5)

The 5.5 `BlueprintEditorLibrary` page does not document `get_node_pos` or
`list_all_pins`. Those methods are therefore runtime-probed and become usable
only after an actual call succeeds; newer-version documentation is not treated
as evidence for ARK DevKit 5.5.4.

## Contracts and commands

Request schema:

```text
blueprint-to-code.arkdev-node-binding-request/v1
```

Result schema:

```text
blueprint-to-code.arkdev-node-binding-result/v1
```

Explicit diagnostic request (locator fields still come only from current
Evidence):

```powershell
python scripts\build_arkdev_node_binding_request.py `
  --asset DinoAncestryOverlay `
  --graph-ref "bp://.../g/..." `
  --node-ref "bp://.../g/.../n/..." `
  --output ".arkdev-probe\node-binding-request.json"
```

Patch Plan mode accepts only the current `DRAFT` or `CONFIRMED` plan and
extracts its existing `nodeRef` entries. Both modes re-read current Evidence;
callers cannot override object name, class, GUID, position, or Pin signatures.

After a future successful DevKit run, validate the bound request/result pair:

```powershell
python scripts\validate_arkdev_node_binding_result.py `
  --request ".arkdev-probe\node-binding-request.json" `
  --result ".arkdev-probe\node-binding-result.json"
```

## Current Evidence checkpoint

The best non-sensitive structural candidate was:

```text
asset: DinoAncestryOverlay
objectPath: /Game/PrimalEarth/UI/Inventory/DinoAncestryOverlay.DinoAncestryOverlay
revision: d411f1518e71579befa00be8
graphRef: bp://027696a62b170f15500a2264@d411f1518e71579befa00be8/g/107
graph: EventGraph
nodes/pins: 13 / 39
required families present: Event, Branch, Function Call, Variable Get
exec and data Pins: present
```

Freshness verification bound the v2 source manifest to the current asset bytes,
but this compatibility generation is not release authority and has no public
authority manifest SHA. The five-node builder preflight therefore returned
`ARKDEV_NODE_BINDING_REQUEST=ERROR:EVIDENCE_NOT_AUTHORITATIVE` and left both
request and result files absent. Independently, all 13 candidate nodes have an
empty Evidence `node_guid`. A bounded audit of all 312 local indexed captures
also found zero non-empty `nodes.extra_json.node_guid` values.

The candidate `.uasset` pre-run/current SHA-256 is:

```text
8772a57d963a73f57656d30296c00511fe7fb4fd5811acdaa0d739e894471129
```

No request was emitted, no DevKit script was run, and no ARK asset was modified.
`MANUAL_RUNS=0` is therefore the truthful result for this phase attempt.

## Gate to resume

Resume the one-run checkpoint only after a new current Evidence generation
provides, for 5 through 12 nodes in one graph:

1. a current manifest SHA;
2. non-zero confirmed 32-hex `NodeGuid` values;
3. current object names/classes and Pin signatures;
4. source bytes that still verify as `FRESH`.

At that point the user checkpoint remains exactly one run: save the target
Blueprint, execute `arkdev_evidence_node_binding_probe.py`, and reply `已运行`.
Phase 3D still requires every requested node to be `EXACT`, representative live
position access, deterministic/path-free output, and unchanged asset bytes.
Phase 4A remains blocked until live Pin identity has a separately verified
adapter; Pin signature agreement alone is insufficient.
