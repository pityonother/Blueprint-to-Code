#pragma once

#include "CoreMinimal.h"
#include "Modules/ModuleManager.h"

class FBlueprintToCodeExporterModule : public IModuleInterface
{
public:
    virtual void StartupModule() override;
    virtual void ShutdownModule() override;

private:
    void RegisterMenus();
    void ExportSelectedBlueprints(const struct FToolMenuContext& MenuContext);

    bool ExportBlueprint(class UBlueprint* Blueprint, const struct FAssetData& AssetData, FString& OutReportPath, FString& OutError) const;
    FString ResolveProjectRoot() const;
    FString MakeCaptureDirectoryName(const FString& RawName) const;
    FString ClassifyGraph(const class UBlueprint* Blueprint, const class UEdGraph* Graph) const;
};
