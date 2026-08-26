#pragma once

#include "Containers/Ticker.h"
#include "CoreMinimal.h"
#include "Modules/ModuleManager.h"

class FJsonObject;

class FBlueprintToCodeExporterModule : public IModuleInterface
{
public:
    virtual void StartupModule() override;
    virtual void ShutdownModule() override;

private:
    void RegisterMenus();
    void ExportSelectedBlueprints(const struct FToolMenuContext& MenuContext);
    void ShowEditorBridgeStatus(const struct FToolMenuContext& MenuContext);
    void WriteEditorStateSnapshotNow(const struct FToolMenuContext& MenuContext);

    void StartEditorBridge();
    void StopEditorBridge();
    bool TickEditorBridge(float DeltaTime);
    bool WriteEditorStateSnapshot(bool bForce);
    bool BuildEditorStateSnapshot(TSharedRef<FJsonObject>& OutSnapshot, FString& OutSemanticState) const;

    bool ExportBlueprint(class UBlueprint* Blueprint, const struct FAssetData& AssetData, FString& OutReportPath, FString& OutError) const;
    FString ResolveProjectRoot() const;
    FString MakeCaptureDirectoryName(const FString& RawName) const;
    FString ClassifyGraph(const class UBlueprint* Blueprint, const class UEdGraph* Graph) const;

    FTSTicker::FDelegateHandle EditorBridgeTickerHandle;
    FGuid EditorBridgeInstanceId;
    uint64 EditorBridgeSequence = 0;
    double LastBridgeWriteSeconds = 0.0;
    FString LastBridgeSemanticState;
    FString EditorBridgeStatePath;
};
