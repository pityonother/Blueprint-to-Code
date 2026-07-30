#include "BlueprintToCodeExporterModule.h"

#include "AssetRegistry/AssetData.h"
#include "ContentBrowserModule.h"
#include "EdGraph/EdGraph.h"
#include "Engine/Blueprint.h"
#include "Framework/Notifications/NotificationManager.h"
#include "HAL/PlatformProcess.h"
#include "Interfaces/IPluginManager.h"
#include "IContentBrowserSingleton.h"
#include "Misc/FileHelper.h"
#include "Misc/MessageDialog.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "ToolMenus.h"
#include "Widgets/Notifications/SNotificationList.h"

#define LOCTEXT_NAMESPACE "FBlueprintToCodeExporterModule"

namespace
{
const TCHAR* ExportSchema = TEXT("blueprint-translator.graph-pages.cpp.v1");

template <typename GraphArrayType>
bool ArrayContainsGraph(const GraphArrayType& Graphs, const UEdGraph* Graph)
{
    return Graphs.ContainsByPredicate([Graph](const auto& Candidate)
    {
        return Candidate == Graph;
    });
}

FString NormalizeFilenamePart(const FString& Value)
{
    FString Result;
    Result.Reserve(Value.Len());
    for (const TCHAR Character : Value)
    {
        if (FChar::IsAlnum(Character) || Character == TEXT('_') || Character == TEXT('-') || Character == TEXT('.'))
        {
            Result.AppendChar(Character);
        }
        else
        {
            Result.AppendChar(TEXT('_'));
        }
    }
    return Result.IsEmpty() ? TEXT("Blueprint") : Result;
}

void AddStringField(TSharedRef<FJsonObject> Object, const FString& Name, const FString& Value)
{
    Object->SetStringField(Name, Value);
}

FString JsonObjectToString(const TSharedRef<FJsonObject>& Object)
{
    FString Output;
    const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Output);
    FJsonSerializer::Serialize(Object, Writer);
    return Output;
}
}

void FBlueprintToCodeExporterModule::StartupModule()
{
    if (UToolMenus::IsToolMenuUIEnabled())
    {
        UToolMenus::RegisterStartupCallback(
            FSimpleMulticastDelegate::FDelegate::CreateRaw(this, &FBlueprintToCodeExporterModule::RegisterMenus)
        );
    }
}

void FBlueprintToCodeExporterModule::ShutdownModule()
{
    if (UToolMenus::IsToolMenuUIEnabled())
    {
        UToolMenus::UnRegisterStartupCallback(this);
        UToolMenus::UnregisterOwner(this);
    }
}

void FBlueprintToCodeExporterModule::RegisterMenus()
{
    UToolMenu* ToolsMenu = UToolMenus::Get()->ExtendMenu(TEXT("LevelEditor.MainMenu.Tools"));
    FToolMenuSection& Section = ToolsMenu->FindOrAddSection(TEXT("BlueprintToCode"));
    Section.Label = LOCTEXT("BlueprintToCodeSection", "Blueprint to Code");
    Section.AddMenuEntry(
        TEXT("BlueprintToCodeExportSelectedGraphQueue"),
        LOCTEXT("ExportSelectedGraphQueueLabel", "Export Selected Blueprint Graph Queue"),
        LOCTEXT("ExportSelectedGraphQueueTooltip", "Export graph page names for selected Blueprint assets into the Blueprint to Code captures folder."),
        FSlateIcon(),
        FToolMenuExecuteAction::CreateRaw(this, &FBlueprintToCodeExporterModule::ExportSelectedBlueprints)
    );
}

void FBlueprintToCodeExporterModule::ExportSelectedBlueprints(const FToolMenuContext& MenuContext)
{
    (void)MenuContext;

    FContentBrowserModule& ContentBrowserModule = FModuleManager::LoadModuleChecked<FContentBrowserModule>(TEXT("ContentBrowser"));

    TArray<FAssetData> SelectedAssets;
    ContentBrowserModule.Get().GetSelectedAssets(SelectedAssets);

    if (SelectedAssets.Num() == 0)
    {
        FMessageDialog::Open(EAppMsgType::Ok, LOCTEXT("NoAssetSelected", "Select one or more Blueprint assets in the Content Browser first."));
        return;
    }

    int32 ExportedCount = 0;
    TArray<FString> ReportPaths;
    TArray<FString> Errors;

    for (const FAssetData& AssetData : SelectedAssets)
    {
        UObject* Asset = AssetData.GetAsset();
        UBlueprint* Blueprint = Cast<UBlueprint>(Asset);
        if (!Blueprint)
        {
            continue;
        }

        FString ReportPath;
        FString Error;
        if (ExportBlueprint(Blueprint, AssetData, ReportPath, Error))
        {
            ++ExportedCount;
            ReportPaths.Add(ReportPath);
        }
        else if (!Error.IsEmpty())
        {
            Errors.Add(Error);
        }
    }

    FString Message;
    if (ExportedCount > 0)
    {
        Message = FString::Printf(TEXT("Exported graph queues for %d Blueprint asset(s)."), ExportedCount);
        if (ReportPaths.Num() > 0)
        {
            Message += LINE_TERMINATOR;
            Message += TEXT("Report: ");
            Message += ReportPaths[0];
        }
    }
    else
    {
        Message = TEXT("No Blueprint graph queues were exported. Select Blueprint assets, not generated classes or folders.");
    }

    if (Errors.Num() > 0)
    {
        Message += LINE_TERMINATOR LINE_TERMINATOR TEXT("Errors:");
        for (const FString& Error : Errors)
        {
            Message += LINE_TERMINATOR TEXT("- ");
            Message += Error;
        }
    }

    FNotificationInfo Notification(FText::FromString(Message));
    Notification.ExpireDuration = 8.0f;
    Notification.bUseLargeFont = false;
    FSlateNotificationManager::Get().AddNotification(Notification);

    FMessageDialog::Open(EAppMsgType::Ok, FText::FromString(Message));
}

bool FBlueprintToCodeExporterModule::ExportBlueprint(UBlueprint* Blueprint, const FAssetData& AssetData, FString& OutReportPath, FString& OutError) const
{
    if (!Blueprint)
    {
        OutError = TEXT("Null Blueprint asset.");
        return false;
    }

    TArray<UEdGraph*> Graphs;
    Blueprint->GetAllGraphs(Graphs);

    const FString ProjectRoot = ResolveProjectRoot();
    const FString CaptureName = MakeCaptureDirectoryName(Blueprint->GetName());
    const FString CaptureDir = FPaths::Combine(ProjectRoot, TEXT("captures"), CaptureName);
    IFileManager::Get().MakeDirectory(*CaptureDir, true);

    if (!FPaths::DirectoryExists(CaptureDir))
    {
        OutError = FString::Printf(TEXT("Could not create capture directory: %s"), *CaptureDir);
        return false;
    }

    TArray<FString> QueueLines;
    TArray<FString> ReportLines;
    TArray<TSharedPtr<FJsonValue>> GraphValues;

    ReportLines.Add(TEXT("# Blueprint To Code C++ Graph Export Report"));
    ReportLines.Add(TEXT(""));
    ReportLines.Add(FString::Printf(TEXT("- Schema: `%s`"), ExportSchema));
    ReportLines.Add(FString::Printf(TEXT("- Blueprint: `%s`"), *Blueprint->GetName()));
    ReportLines.Add(FString::Printf(TEXT("- Asset path: `%s`"), *AssetData.GetSoftObjectPath().ToString()));
    ReportLines.Add(FString::Printf(TEXT("- Output root: `%s`"), *ProjectRoot));
    ReportLines.Add(FString::Printf(TEXT("- Graph pages exported: %d"), Graphs.Num()));
    ReportLines.Add(TEXT(""));
    ReportLines.Add(TEXT("## Graph Pages"));
    ReportLines.Add(TEXT(""));
    ReportLines.Add(TEXT("| Name | Type | Nodes | Schema |"));
    ReportLines.Add(TEXT("| --- | --- | ---: | --- |"));

    for (UEdGraph* Graph : Graphs)
    {
        if (!Graph)
        {
            continue;
        }

        const FString GraphName = Graph->GetName();
        const FString GraphType = ClassifyGraph(Blueprint, Graph);
        const UClass* SchemaClass = Graph->Schema.Get();
        const FString SchemaName = SchemaClass ? SchemaClass->GetName() : FString();
        const int32 NodeCount = Graph->Nodes.Num();

        QueueLines.Add(FString::Printf(TEXT("%s | %s"), *GraphName, *GraphType));
        ReportLines.Add(FString::Printf(TEXT("| %s | %s | %d | %s |"), *GraphName, *GraphType, NodeCount, *SchemaName));

        TSharedRef<FJsonObject> GraphObject = MakeShared<FJsonObject>();
        AddStringField(GraphObject, TEXT("name"), GraphName);
        AddStringField(GraphObject, TEXT("type"), GraphType);
        GraphObject->SetNumberField(TEXT("node_count"), NodeCount);
        AddStringField(GraphObject, TEXT("schema_class"), SchemaName);
        AddStringField(GraphObject, TEXT("path_name"), Graph->GetPathName());
        GraphValues.Add(MakeShared<FJsonValueObject>(GraphObject));
    }

    TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
    AddStringField(Root, TEXT("schema"), ExportSchema);
    AddStringField(Root, TEXT("asset_name"), Blueprint->GetName());
    AddStringField(Root, TEXT("asset_path"), AssetData.GetSoftObjectPath().ToString());
    AddStringField(Root, TEXT("generated_class"), Blueprint->GeneratedClass ? Blueprint->GeneratedClass->GetName() : FString());
    AddStringField(Root, TEXT("skeleton_generated_class"), Blueprint->SkeletonGeneratedClass ? Blueprint->SkeletonGeneratedClass->GetName() : FString());
    Root->SetNumberField(TEXT("graph_count"), GraphValues.Num());
    Root->SetArrayField(TEXT("graphs"), GraphValues);

    const FString JsonPath = FPaths::Combine(CaptureDir, TEXT("graph_pages_cpp.json"));
    const FString QueuePath = FPaths::Combine(CaptureDir, TEXT("graph_queue.txt"));
    const FString ReportPath = FPaths::Combine(CaptureDir, TEXT("cpp_export_report.md"));

    const bool bJsonSaved = FFileHelper::SaveStringToFile(JsonObjectToString(Root), *JsonPath);
    const bool bQueueSaved = FFileHelper::SaveStringArrayToFile(QueueLines, *QueuePath);
    const bool bReportSaved = FFileHelper::SaveStringArrayToFile(ReportLines, *ReportPath);

    if (!bJsonSaved || !bQueueSaved || !bReportSaved)
    {
        OutError = FString::Printf(TEXT("Failed to write one or more export files under: %s"), *CaptureDir);
        return false;
    }

    OutReportPath = ReportPath;
    return true;
}

FString FBlueprintToCodeExporterModule::ResolveProjectRoot() const
{
    FString EnvRoot = FPlatformMisc::GetEnvironmentVariable(TEXT("BLUEPRINT_TO_CODE_ROOT"));
    if (!EnvRoot.IsEmpty())
    {
        EnvRoot = FPaths::ConvertRelativePathToFull(EnvRoot);
        FPaths::NormalizeDirectoryName(EnvRoot);
        if (!EnvRoot.IsEmpty())
        {
            return EnvRoot;
        }
    }

    const TSharedPtr<IPlugin> Plugin = IPluginManager::Get().FindPlugin(TEXT("BlueprintToCodeExporter"));
    if (Plugin.IsValid())
    {
        FString Candidate = FPaths::ConvertRelativePathToFull(Plugin->GetBaseDir());
        for (int32 Index = 0; Index < 8 && !Candidate.IsEmpty(); ++Index)
        {
            if (FPaths::FileExists(FPaths::Combine(Candidate, TEXT("scripts"), TEXT("bp_clipboard_to_prompt.py"))))
            {
                FPaths::NormalizeDirectoryName(Candidate);
                return Candidate;
            }
            Candidate = FPaths::GetPath(Candidate);
        }
    }

    FString FallbackRoot = FPaths::ConvertRelativePathToFull(
        FPaths::Combine(
            FPlatformProcess::UserDir(),
            TEXT("Documents"),
            TEXT("Blueprint to Code")
        )
    );
    FPaths::NormalizeDirectoryName(FallbackRoot);
    return FallbackRoot;
}

FString FBlueprintToCodeExporterModule::MakeCaptureDirectoryName(const FString& RawName) const
{
    return NormalizeFilenamePart(RawName);
}

FString FBlueprintToCodeExporterModule::ClassifyGraph(const UBlueprint* Blueprint, const UEdGraph* Graph) const
{
    if (!Blueprint || !Graph)
    {
        return TEXT("Unknown");
    }

    if (ArrayContainsGraph(Blueprint->UbergraphPages, Graph))
    {
        return TEXT("EventGraph");
    }

    if (Blueprint->UserConstructionScript == Graph)
    {
        return TEXT("ConstructionScript");
    }

    if (ArrayContainsGraph(Blueprint->FunctionGraphs, Graph))
    {
        return TEXT("Function");
    }

    if (ArrayContainsGraph(Blueprint->MacroGraphs, Graph))
    {
        return TEXT("Macro");
    }

    if (ArrayContainsGraph(Blueprint->DelegateSignatureGraphs, Graph))
    {
        return TEXT("DelegateSignature");
    }

    if (ArrayContainsGraph(Blueprint->IntermediateGeneratedGraphs, Graph))
    {
        return TEXT("Intermediate");
    }

    return TEXT("Unknown");
}

#undef LOCTEXT_NAMESPACE

IMPLEMENT_MODULE(FBlueprintToCodeExporterModule, BlueprintToCodeExporter)
