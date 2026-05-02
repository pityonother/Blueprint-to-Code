using UnrealBuildTool;

public class BlueprintToCodeExporter : ModuleRules
{
    public BlueprintToCodeExporter(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = ModuleRules.PCHUsageMode.UseExplicitOrSharedPCHs;

        PublicDependencyModuleNames.AddRange(
            new string[]
            {
                "Core",
                "CoreUObject",
                "Engine"
            }
        );

        PrivateDependencyModuleNames.AddRange(
            new string[]
            {
                "AssetRegistry",
                "ContentBrowser",
                "Json",
                "Projects",
                "Slate",
                "SlateCore",
                "ToolMenus",
                "UnrealEd"
            }
        );
    }
}
