"""Static ARK/Unreal Blueprint translator configuration."""

from __future__ import annotations

KEYWORD_GROUPS: dict[str, list[str]] = {
    "Unreal common": [
        "BeginPlay",
        "Tick",
        "ConstructionScript",
        "Event",
        "Function",
        "Macro",
        "Branch",
        "Sequence",
        "Timeline",
        "Delay",
        "Timer",
        "Interface",
        "Cast",
        "Replication",
        "Server",
        "Client",
        "Multicast",
        "Authority",
        "Owner",
        "Instigator",
    ],
    "ARK/ASA common": [
        "Primal",
        "Shooter",
        "Dino",
        "Character",
        "Structure",
        "Inventory",
        "Item",
        "Buff",
        "Status",
        "Tribe",
        "Team",
        "Targeting",
        "Tamed",
        "Baby",
        "Mating",
        "Food",
        "Water",
        "Fuel",
        "Crafting",
        "MultiUse",
        "Radial",
        "Stasis",
        "Octree",
    ],
    "Range/detection": [
        "Radius",
        "Range",
        "Sphere",
        "Collision",
        "Overlap",
        "Trace",
        "Nearby",
        "Register",
        "Unregister",
        "Refresh",
        "Query",
        "Container",
    ],
    "Network/save": [
        "RepNotify",
        "SaveGame",
        "Net",
        "Dormancy",
        "Stasis",
        "DedicatedServer",
    ],
}

PROFILE_CONFIG: dict[str, dict[str, object]] = {
    "unreal": {
        "groups": ["Unreal common", "Network/save"],
        "extra": ["Blueprint", "Kismet", "Actor", "Component", "WorldContextObject"],
        "focus": "General Unreal Blueprint behavior, control flow, data dependencies, and engine semantics.",
    },
    "ark": {
        "groups": list(KEYWORD_GROUPS),
        "extra": ["BlueprintGeneratedClass", "ShooterGame", "PrimalGameData"],
        "focus": "General ARK/ASA DevKit gameplay logic, Blueprint inheritance, inventory, networking, and stasis behavior.",
    },
    "structure": {
        "groups": list(KEYWORD_GROUPS),
        "extra": ["PrimalStructure", "Structure", "Foundation", "Placement", "Snap", "Demolish", "Power", "Fuel"],
        "focus": "Structure runtime behavior, placement, inventory, fuel, power, radial menu, and range checks.",
    },
    "dino": {
        "groups": list(KEYWORD_GROUPS),
        "extra": ["PrimalDinoCharacter", "Dino", "Tamed", "Baby", "Mating", "TargetingTeam", "AIController"],
        "focus": "Dino character behavior, inventory, baby/mating state, targeting, AI, and server-side runtime logic.",
    },
    "inventory": {
        "groups": list(KEYWORD_GROUPS),
        "extra": ["PrimalInventory", "InventoryRefresh", "ItemQuantity", "Crafting", "RemoteInventory", "DefaultInventoryItems"],
        "focus": "Inventory refresh, item transfer, crafting, default inventory, and UI/runtime inventory state.",
    },
    "buff": {
        "groups": list(KEYWORD_GROUPS),
        "extra": ["PrimalBuff", "Buff", "StatusComponent", "AddBuff", "RemoveBuff", "BuffTick", "DamageType"],
        "focus": "Buff lifecycle, status modifiers, timers, server authority, and replication side effects.",
    },
    "ui": {
        "groups": ["Unreal common", "ARK/ASA common"],
        "extra": ["Widget", "HUD", "UI", "InventoryUI", "Button", "Text", "Canvas", "OnClicked"],
        "focus": "UI/widget behavior, user interaction, inventory views, and client-only presentation logic.",
    },
    "networking": {
        "groups": ["Unreal common", "Network/save", "ARK/ASA common"],
        "extra": ["Authority", "Role", "RemoteRole", "Replicated", "RPC", "Server", "Client", "Multicast", "Dormancy"],
        "focus": "Server/client authority, RPCs, replication, dormancy, save-game state, and multiplayer correctness.",
    },
    "feeding": {
        "groups": list(KEYWORD_GROUPS),
        "extra": [
            "Feeding",
            "FeedingTrough",
            "Trough",
            "Maewing",
            "FoodContainer",
            "DinoFoodContainer",
            "BabyFood",
            "TamedDino",
            "NearbyDino",
            "FeedRadius",
        ],
        "focus": "Food delivery, feeding range, visual radius, inventory containers, baby/tamed dino checks, and stasis/octree registration.",
    },
}

NODE_SEMANTICS: dict[str, str] = {
    "K2Node_CallFunction": "Calls a Blueprint or native function. Exec pins control when it runs; input pins are parameters; output pins are return values.",
    "K2Node_VariableGet": "Reads a variable and supplies its value to connected data pins.",
    "K2Node_VariableSet": "Writes a variable. The value input should be traced through data flow.",
    "K2Node_VariableSetRef": "Writes through a variable reference pin, often mutating a struct, object reference, or by-ref parameter.",
    "K2Node_AssignmentStatement": "Assigns a value to a variable/property-style target, commonly emitted for expanded Blueprint assignment nodes.",
    "K2Node_Event": "Entry point fired by an engine or Blueprint event.",
    "K2Node_CustomEvent": "User-defined event entry point that can be called or bound as a delegate.",
    "K2Node_FunctionEntry": "Function graph entry point.",
    "K2Node_FunctionResult": "Function return node.",
    "K2Node_IfThenElse": "Branch node. The Condition input controls then/else exec outputs.",
    "K2Node_ExecutionSequence": "Runs multiple exec outputs in order.",
    "K2Node_DynamicCast": "Runtime type check/cast with success and failure execution paths.",
    "K2Node_MacroInstance": "Expands a Blueprint macro such as IsValid, ForEachLoop, DoOnce, Gate, or Delay-like control.",
    "K2Node_Timeline": "Timeline update/finished flow for time-based curves.",
    "K2Node_SpawnActorFromClass": "Spawns an actor instance at runtime.",
    "K2Node_ConstructObjectFromClass": "Constructs a UObject instance at runtime.",
    "K2Node_CreateDelegate": "Creates a delegate binding.",
    "K2Node_AddDelegate": "Binds an event/delegate handler to a multicast delegate such as ARK character/component events.",
    "K2Node_RemoveDelegate": "Unbinds a specific event/delegate handler from a multicast delegate.",
    "K2Node_ClearDelegate": "Clears bound handlers from a delegate.",
    "K2Node_ComponentBoundEvent": "Entry point fired by a component delegate/event.",
    "K2Node_CallParentFunction": "Calls the parent class implementation of the current Blueprint override, usually preserving inherited ARK/Unreal behavior.",
    "K2Node_AddComponent": "Adds or creates an actor component.",
    "K2Node_SwitchEnum": "Exec switch based on enum value.",
    "K2Node_SwitchInteger": "Exec switch based on an integer value.",
    "K2Node_SwitchName": "Exec switch based on Name value.",
    "K2Node_SwitchString": "Exec switch based on String value.",
    "K2Node_SwitchAuthority": "Branches execution by network authority, separating server-authoritative and remote/client paths.",
    "K2Node_Select": "Selects a value from multiple data inputs based on an index/condition.",
    "K2Node_Knot": "Reroute node used for graph layout; it forwards exec or data.",
    "K2Node_Self": "Reference to the current Blueprint instance.",
    "K2Node_Tunnel": "Entry/exit tunnel for collapsed graphs, macros, or function-like graph boundaries.",
    "K2Node_Composite": "Collapsed graph container; internal behavior lives inside the expanded/collapsed subgraph.",
    "K2Node_InputAction": "Input action event entry point from Unreal input bindings.",
    "K2Node_InputKey": "Input key event entry point from Unreal input bindings.",
    "K2Node_EnumEquality": "Pure enum comparison; behaves like an equality condition in data flow.",
    "K2Node_PromotableOperator": "Math or comparison operator that may be pure data-flow logic.",
    "K2Node_CommutativeAssociativeBinaryOperator": "Math/boolean operator combining multiple inputs.",
    "K2Node_MakeArray": "Builds an array value from input elements.",
    "K2Node_GetArrayItem": "Reads one array element by index.",
    "K2Node_MakeMap": "Builds a map value from key/value input pairs.",
    "K2Node_MakeSet": "Builds a set value from input elements.",
    "K2Node_MakeStruct": "Builds a struct value from field input pins.",
    "K2Node_BreakStruct": "Breaks a struct value into field output pins.",
    "K2Node_CallArrayFunction": "Calls an array helper operation such as Add, Remove, Length, Find, or Contains.",
}

ARK_GLOSSARY: dict[str, str] = {
    "PrimalItem": "ARK item Blueprint/class used for inventory items, consumables, resources, structures, and crafted objects.",
    "PrimalInventory": "Inventory component/class that owns item lists, crafting, remote inventory access, and refresh behavior.",
    "PrimalInventoryComponent": "Runtime component that stores and manages items for characters, structures, and containers.",
    "PrimalStructure": "Base class family for placeable structures, including ownership, placement, multi-use, inventory, and network state.",
    "PrimalDinoCharacter": "Base dino character class with tame, baby, mating, AI, targeting, food, and status behavior.",
    "ShooterCharacter": "Player character class family used by ARK/ShooterGame.",
    "Buff": "Status effect object that can apply timed or persistent behavior to characters, dinos, items, or structures.",
    "StatusComponent": "Component that stores character/dino status values such as health, stamina, food, water, and torpor.",
    "MultiUse": "ARK radial menu / interaction system for context actions on actors and structures.",
    "Tribe": "Player group ownership and permission system.",
    "TargetingTeam": "Team/faction identifier used for targeting, AI, tribe ownership, and friendly/hostile checks.",
    "Stasis": "ARK optimization state where actors outside active relevance may be paused or reduced in simulation.",
    "Octree": "Spatial indexing structure commonly used for fast nearby/range queries.",
    "Replication": "Unreal network synchronization from server to clients.",
    "NetDormancy": "Unreal networking mode that reduces replication frequency for actors with stable state.",
}

CONTEXT_TEMPLATE = """# Blueprint Translator Sidecar Context

Asset name:
Parent class:
Interfaces:
Tags:

## Components
- Component name:
  - Class:
  - Purpose:
  - Defaults:
    - ExampleProperty: ExampleValue

## Class Defaults
- Replication:
- Inventory:
- Stasis:
- Octree:
- Radius:
- Range:
- Food:
- Buff:
- MultiUse:

## Test Observations
- What was observed in PIE / editor / live game:
- What changed after edits:
- Remaining uncertainty:

## Asset Directory Layout
```text
MyBlueprint/
  manifest.json
  graphs/
    EventGraph.txt
    Function_DoSomething.txt
  defaults.json
  components.json
  notes.md
```
"""
