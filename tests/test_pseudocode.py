import importlib.util
import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bp_clipboard_to_prompt.py"
FIXTURES = ROOT / "tests" / "fixtures"


def load_translator():
    spec = importlib.util.spec_from_file_location("bp_translator", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def parse_fixture(bp, name):
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return bp.parse_blueprint_text(
        text=text,
        source=name,
        asset_name="TestAsset",
        graph_name="EventGraph",
        keywords=bp.profile_keywords("ark", []),
    )


class PseudocodeTests(unittest.TestCase):
    def test_branch_then_call_is_nested_and_pure_get_is_not_statement(self):
        bp = load_translator()
        _, nodes, payload = parse_fixture(bp, "pseudocode_branch.txt")
        pseudocode = bp.render_pseudocode(nodes, payload["exec_flow"], payload["data_flow"])
        self.assertIn("if bIsSleeping:", pseudocode)
        self.assertRegex(pseudocode, r"if bIsSleeping:\n\s+InventoryRefresh\(\)")
        self.assertNotIn("read bIsSleeping", pseudocode)

    def test_recursive_expression_for_branch_condition(self):
        bp = load_translator()
        _, nodes, payload = parse_fixture(bp, "recursive_expression.txt")
        branch = payload["data_flow"]["branch_conditions"][0]
        self.assertEqual(branch["source"], "FeedingRange > 3000.0")
        pseudocode = bp.render_pseudocode(nodes, payload["exec_flow"], payload["data_flow"])
        self.assertIn("if FeedingRange > 3000.0:", pseudocode)
        self.assertNotIn("Greater_FloatFloat(A, B)", pseudocode)

    def test_sequence_exec_order_is_stable(self):
        bp = load_translator()
        _, nodes, payload = parse_fixture(bp, "sequence_flow.txt")
        ordered = payload["exec_flow"]["ordered_node_names"]
        self.assertLess(ordered.index("K2Node_ExecutionSequence_0"), ordered.index("K2Node_CallFunction_0"))
        self.assertLess(ordered.index("K2Node_CallFunction_0"), ordered.index("K2Node_CallFunction_1"))
        pseudocode = bp.render_pseudocode(nodes, payload["exec_flow"], payload["data_flow"])
        self.assertLess(pseudocode.index("CallA()"), pseudocode.index("CallB()"))

    def test_server_authority_call_is_rendered_as_control_flow(self):
        bp = load_translator()
        _, nodes, payload = parse_fixture(bp, "real_ark_achatina_inventory_refresh.txt")
        pseudocode = bp.render_pseudocode(nodes, payload["exec_flow"], payload["data_flow"])
        self.assertIn("if running on server/authority:", pseudocode)
        self.assertIn("<missing linked node K2Node_MacroInstance_0 from IsRunningOnServer.Yes>", pseudocode)

    def test_macro_instance_name_is_parsed_and_rendered(self):
        bp = load_translator()
        text = """
Begin Object Class=/Script/BlueprintGraph.K2Node_Event Name="K2Node_Event_0"
   EventReference=(MemberName="ReceiveBeginPlay")
   CustomProperties Pin (PinId=GUID_A,PinName="then",Direction="EGPD_Output",PinType.PinCategory="exec",LinkedTo=(K2Node_MacroInstance_0 GUID_B,),PersistentGuid=GUID_Z,)
End Object
Begin Object Class=/Script/BlueprintGraph.K2Node_MacroInstance Name="K2Node_MacroInstance_0"
   MacroGraphReference=(MacroGraph="/Script/Engine.EdGraph'/Engine/EditorBlueprintResources/StandardMacros.StandardMacros:ForEachLoop'")
   CustomProperties Pin (PinId=GUID_B,PinName="Exec",PinType.PinCategory="exec",LinkedTo=(K2Node_Event_0 GUID_A,),PersistentGuid=GUID_Z,)
   CustomProperties Pin (PinId=GUID_C,PinName="Loop Body",Direction="EGPD_Output",PinType.PinCategory="exec",LinkedTo=(K2Node_CallFunction_0 GUID_D,),PersistentGuid=GUID_Z,)
   CustomProperties Pin (PinId=GUID_E,PinName="Completed",Direction="EGPD_Output",PinType.PinCategory="exec",LinkedTo=(K2Node_CallFunction_1 GUID_F,),PersistentGuid=GUID_Z,)
   CustomProperties Pin (PinId=GUID_G,PinName="Array",PinType.PinCategory="wildcard",LinkedTo=(K2Node_VariableGet_0 GUID_H,),PersistentGuid=GUID_Z,)
   CustomProperties Pin (PinId=GUID_I,PinName="Array Element",Direction="EGPD_Output",PinType.PinCategory="wildcard",PersistentGuid=GUID_Z,)
End Object
Begin Object Class=/Script/BlueprintGraph.K2Node_VariableGet Name="K2Node_VariableGet_0"
   VariableReference=(MemberName="Items",bSelfContext=True)
   CustomProperties Pin (PinId=GUID_H,PinName="Items",Direction="EGPD_Output",PinType.PinCategory="array",LinkedTo=(K2Node_MacroInstance_0 GUID_G,),PersistentGuid=GUID_Z,)
End Object
Begin Object Class=/Script/BlueprintGraph.K2Node_CallFunction Name="K2Node_CallFunction_0"
   FunctionReference=(MemberName="HandleItem")
   CustomProperties Pin (PinId=GUID_D,PinName="execute",PinType.PinCategory="exec",LinkedTo=(K2Node_MacroInstance_0 GUID_C,),PersistentGuid=GUID_Z,)
End Object
Begin Object Class=/Script/BlueprintGraph.K2Node_CallFunction Name="K2Node_CallFunction_1"
   FunctionReference=(MemberName="FinishLoop")
   CustomProperties Pin (PinId=GUID_F,PinName="execute",PinType.PinCategory="exec",LinkedTo=(K2Node_MacroInstance_0 GUID_E,),PersistentGuid=GUID_Z,)
End Object
"""
        _, nodes, payload = bp.parse_blueprint_text(
            text=text,
            source="macro",
            asset_name="TestAsset",
            graph_name="EventGraph",
            keywords=bp.profile_keywords("ark", []),
        )
        macro = payload["macros"][0]
        self.assertEqual(macro["macro"], "ForEachLoop")
        pseudocode = bp.render_pseudocode(nodes, payload["exec_flow"], payload["data_flow"])
        self.assertIn("for each Array Element in Items:", pseudocode)
        self.assertRegex(pseudocode, r"for each Array Element in Items:\n\s+HandleItem\(\)")
        self.assertRegex(pseudocode, r"after loop:\n\s+FinishLoop\(\)")

    def test_isvalid_macro_is_rendered_as_branch(self):
        bp = load_translator()
        text = """
Begin Object Class=/Script/BlueprintGraph.K2Node_Event Name="K2Node_Event_0"
   EventReference=(MemberName="ReceiveBeginPlay")
   CustomProperties Pin (PinId=GUID_A,PinName="then",Direction="EGPD_Output",PinType.PinCategory="exec",LinkedTo=(K2Node_MacroInstance_0 GUID_B,),PersistentGuid=GUID_Z,)
End Object
Begin Object Class=/Script/BlueprintGraph.K2Node_MacroInstance Name="K2Node_MacroInstance_0"
   MacroGraphReference=(MacroGraph="/Script/Engine.EdGraph'/Engine/EditorBlueprintResources/StandardMacros.StandardMacros:IsValid'")
   CustomProperties Pin (PinId=GUID_B,PinName="Exec",PinType.PinCategory="exec",LinkedTo=(K2Node_Event_0 GUID_A,),PersistentGuid=GUID_Z,)
   CustomProperties Pin (PinId=GUID_C,PinName="InputObject",PinType.PinCategory="object",LinkedTo=(K2Node_VariableGet_0 GUID_D,),PersistentGuid=GUID_Z,)
   CustomProperties Pin (PinId=GUID_E,PinName="Is Valid",Direction="EGPD_Output",PinType.PinCategory="exec",LinkedTo=(K2Node_CallFunction_0 GUID_F,),PersistentGuid=GUID_Z,)
   CustomProperties Pin (PinId=GUID_G,PinName="Is Not Valid",Direction="EGPD_Output",PinType.PinCategory="exec",LinkedTo=(K2Node_CallFunction_1 GUID_H,),PersistentGuid=GUID_Z,)
End Object
Begin Object Class=/Script/BlueprintGraph.K2Node_VariableGet Name="K2Node_VariableGet_0"
   VariableReference=(MemberName="MyInventoryComponent",bSelfContext=True)
   CustomProperties Pin (PinId=GUID_D,PinName="MyInventoryComponent",Direction="EGPD_Output",PinType.PinCategory="object",LinkedTo=(K2Node_MacroInstance_0 GUID_C,),PersistentGuid=GUID_Z,)
End Object
Begin Object Class=/Script/BlueprintGraph.K2Node_CallFunction Name="K2Node_CallFunction_0"
   FunctionReference=(MemberName="UseInventory")
   CustomProperties Pin (PinId=GUID_F,PinName="execute",PinType.PinCategory="exec",LinkedTo=(K2Node_MacroInstance_0 GUID_E,),PersistentGuid=GUID_Z,)
End Object
Begin Object Class=/Script/BlueprintGraph.K2Node_CallFunction Name="K2Node_CallFunction_1"
   FunctionReference=(MemberName="LogMissingInventory")
   CustomProperties Pin (PinId=GUID_H,PinName="execute",PinType.PinCategory="exec",LinkedTo=(K2Node_MacroInstance_0 GUID_G,),PersistentGuid=GUID_Z,)
End Object
"""
        _, nodes, payload = bp.parse_blueprint_text(
            text=text,
            source="isvalid",
            asset_name="TestAsset",
            graph_name="EventGraph",
            keywords=bp.profile_keywords("ark", []),
        )
        pseudocode = bp.render_pseudocode(nodes, payload["exec_flow"], payload["data_flow"])
        self.assertRegex(pseudocode, r"if IsValid\(MyInventoryComponent\):\n\s+UseInventory\(\)")
        self.assertRegex(pseudocode, r"else:\n\s+LogMissingInventory\(\)")

    def test_stateful_macros_render_expected_controls(self):
        bp = load_translator()
        text = """
Begin Object Class=/Script/BlueprintGraph.K2Node_Event Name="K2Node_Event_0"
   EventReference=(MemberName="ReceiveBeginPlay")
   CustomProperties Pin (PinId=GUID_A,PinName="then",Direction="EGPD_Output",PinType.PinCategory="exec",LinkedTo=(K2Node_MacroInstance_0 GUID_B,),PersistentGuid=GUID_Z,)
End Object
Begin Object Class=/Script/BlueprintGraph.K2Node_MacroInstance Name="K2Node_MacroInstance_0"
   MacroGraphReference=(MacroGraph="/Script/Engine.EdGraph'/Engine/EditorBlueprintResources/StandardMacros.StandardMacros:DoOnce'")
   CustomProperties Pin (PinId=GUID_B,PinName="Exec",PinType.PinCategory="exec",LinkedTo=(K2Node_Event_0 GUID_A,),PersistentGuid=GUID_Z,)
   CustomProperties Pin (PinId=GUID_C,PinName="Completed",Direction="EGPD_Output",PinType.PinCategory="exec",LinkedTo=(K2Node_MacroInstance_1 GUID_D,),PersistentGuid=GUID_Z,)
End Object
Begin Object Class=/Script/BlueprintGraph.K2Node_MacroInstance Name="K2Node_MacroInstance_1"
   MacroGraphReference=(MacroGraph="/Script/Engine.EdGraph'/Engine/EditorBlueprintResources/StandardMacros.StandardMacros:Gate'")
   CustomProperties Pin (PinId=GUID_D,PinName="Enter",PinType.PinCategory="exec",LinkedTo=(K2Node_MacroInstance_0 GUID_C,),PersistentGuid=GUID_Z,)
   CustomProperties Pin (PinId=GUID_E,PinName="Exit",Direction="EGPD_Output",PinType.PinCategory="exec",LinkedTo=(K2Node_MacroInstance_2 GUID_F,),PersistentGuid=GUID_Z,)
End Object
Begin Object Class=/Script/BlueprintGraph.K2Node_MacroInstance Name="K2Node_MacroInstance_2"
   MacroGraphReference=(MacroGraph="/Script/Engine.EdGraph'/Engine/EditorBlueprintResources/StandardMacros.StandardMacros:Delay'")
   CustomProperties Pin (PinId=GUID_F,PinName="execute",PinType.PinCategory="exec",LinkedTo=(K2Node_MacroInstance_1 GUID_E,),PersistentGuid=GUID_Z,)
   CustomProperties Pin (PinId=GUID_G,PinName="Duration",PinType.PinCategory="float",DefaultValue="2.5",PersistentGuid=GUID_Z,)
   CustomProperties Pin (PinId=GUID_H,PinName="Completed",Direction="EGPD_Output",PinType.PinCategory="exec",LinkedTo=(K2Node_CallFunction_0 GUID_I,),PersistentGuid=GUID_Z,)
End Object
Begin Object Class=/Script/BlueprintGraph.K2Node_CallFunction Name="K2Node_CallFunction_0"
   FunctionReference=(MemberName="AfterDelay")
   CustomProperties Pin (PinId=GUID_I,PinName="execute",PinType.PinCategory="exec",LinkedTo=(K2Node_MacroInstance_2 GUID_H,),PersistentGuid=GUID_Z,)
End Object
"""
        _, nodes, payload = bp.parse_blueprint_text(
            text=text,
            source="stateful_macros",
            asset_name="TestAsset",
            graph_name="EventGraph",
            keywords=bp.profile_keywords("ark", []),
        )
        pseudocode = bp.render_pseudocode(nodes, payload["exec_flow"], payload["data_flow"])
        self.assertIn("do once:", pseudocode)
        self.assertIn("gate Gate:", pseudocode)
        self.assertIn("delay 2.5:", pseudocode)
        self.assertIn("AfterDelay()", pseudocode)


if __name__ == "__main__":
    unittest.main()
