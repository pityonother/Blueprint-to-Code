#include "BlueprintToCodeExporterModule.h"

#include "BlueprintEditorModule.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphSchema.h"
#include "Editor.h"
#include "Engine/Blueprint.h"
#include "Framework/Notifications/NotificationManager.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformTime.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Misc/App.h"
#include "Misc/DateTime.h"
#include "Misc/EngineVersion.h"
#include "Misc/FileHelper.h"
#include "Misc/Guid.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Subsystems/AssetEditorSubsystem.h"
#include "Widgets/Notifications/SNotificationList.h"
#include "UObject/Package.h"
#if PLATFORM_WINDOWS
#include "Windows/WindowsHWrapper.h"
#endif

DEFINE_LOG_CATEGORY_STATIC(LogBlueprintToCodeEditorBridge, Log, All);

namespace
{
constexpr int32 MaxSnapshotNodes = 2000;
constexpr float EditorBridgeTickSeconds = 0.25f;
constexpr double EditorBridgeHeartbeatSeconds = 2.0;

const TCHAR* SnapshotSchema = TEXT("blueprint-to-code.arkdev-editor-snapshot/v1");
const TCHAR* SnapshotProtocol = TEXT("arkdev-editor-snapshot/v1");
const TCHAR* EditorBridgeVersion = TEXT("blueprint-to-code-exporter/editor-bridge-v1");

struct FActiveBlueprintCandidate
{
    UBlueprint* Blueprint = nullptr;
    IAssetEditorInstance* Editor = nullptr;
    double LastActivationTime = 0.0;
    FString ObjectPath;
};

struct FEditorNodeSnapshot
{
    UEdGraphNode* Node = nullptr;
    FString NodeGuid;
};

FString JsonObjectToCompactString(const TSharedRef<FJsonObject>& Object)
{
    FString Output;
    const TSharedRef<TJsonWriter<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>> Writer =
        TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&Output);
    FJsonSerializer::Serialize(Object, Writer);
    return Output;
}

TArray<TSharedPtr<FJsonValue>> StringValues(const TArray<FString>& Values)
{
    TArray<TSharedPtr<FJsonValue>> Result;
    Result.Reserve(Values.Num());
    for (const FString& Value : Values)
    {
        Result.Add(MakeShared<FJsonValueString>(Value));
    }
    return Result;
}

bool AtomicReplaceFile(const FString& DestinationPath, const FString& TemporaryPath)
{
#if PLATFORM_WINDOWS
    return ::MoveFileExW(
        *TemporaryPath,
        *DestinationPath,
        MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH
    ) != 0;
#else
    return false;
#endif
}

FString CompileStatus(const UBlueprint* Blueprint)
{
    if (!Blueprint)
    {
        return TEXT("UNKNOWN");
    }

    switch (Blueprint->Status)
    {
    case BS_Dirty:
        return TEXT("DIRTY");
    case BS_Error:
        return TEXT("ERROR");
    case BS_UpToDate:
    case BS_UpToDateWithWarnings:
        return TEXT("UP_TO_DATE");
    default:
        return TEXT("UNKNOWN");
    }
}

UBlueprint* ResolveActiveBlueprint(FString& OutActivityStatus)
{
    OutActivityStatus = TEXT("NO_OPEN_BLUEPRINT");
    if (!GEditor)
    {
        return nullptr;
    }

    UAssetEditorSubsystem* AssetEditors = GEditor->GetEditorSubsystem<UAssetEditorSubsystem>();
    if (!AssetEditors)
    {
        return nullptr;
    }

    TArray<FActiveBlueprintCandidate> Candidates;
    for (UObject* Asset : AssetEditors->GetAllEditedAssets())
    {
        UBlueprint* Blueprint = Cast<UBlueprint>(Asset);
        if (!Blueprint)
        {
            continue;
        }
        IAssetEditorInstance* Editor = AssetEditors->FindEditorForAsset(Blueprint, false);
        if (!Editor)
        {
            continue;
        }
        FActiveBlueprintCandidate& Candidate = Candidates.AddDefaulted_GetRef();
        Candidate.Blueprint = Blueprint;
        Candidate.Editor = Editor;
        Candidate.LastActivationTime = Editor->GetLastActivationTime();
        Candidate.ObjectPath = Blueprint->GetPathName();
    }

    Candidates.Sort([](const FActiveBlueprintCandidate& Left, const FActiveBlueprintCandidate& Right)
    {
        if (!FMath::IsNearlyEqual(Left.LastActivationTime, Right.LastActivationTime))
        {
            return Left.LastActivationTime > Right.LastActivationTime;
        }
        return Left.ObjectPath < Right.ObjectPath;
    });

    if (Candidates.Num() == 0)
    {
        return nullptr;
    }
    if (
        Candidates.Num() > 1
        && FMath::IsNearlyEqual(
            Candidates[0].LastActivationTime,
            Candidates[1].LastActivationTime
        )
    )
    {
        OutActivityStatus = TEXT("ACTIVE_BLUEPRINT_AMBIGUOUS");
        return nullptr;
    }

    OutActivityStatus = TEXT("ACTIVE_BLUEPRINT");
    return Candidates[0].Blueprint;
}
}

void FBlueprintToCodeExporterModule::StartEditorBridge()
{
    EditorBridgeInstanceId = FGuid::NewGuid();
    EditorBridgeSequence = 0;
    LastBridgeWriteSeconds = 0.0;
    LastBridgeSemanticState.Reset();
    EditorBridgeStatePath = FPaths::Combine(
        ResolveProjectRoot(),
        TEXT(".arkdev-bridge"),
        TEXT("editor_state.json")
    );
    EditorBridgeTickerHandle = FTSTicker::GetCoreTicker().AddTicker(
        FTickerDelegate::CreateRaw(this, &FBlueprintToCodeExporterModule::TickEditorBridge),
        EditorBridgeTickSeconds
    );
    WriteEditorStateSnapshot(true);
}

void FBlueprintToCodeExporterModule::StopEditorBridge()
{
    if (EditorBridgeTickerHandle.IsValid())
    {
        FTSTicker::RemoveTicker(EditorBridgeTickerHandle);
        EditorBridgeTickerHandle.Reset();
    }
    if (!EditorBridgeStatePath.IsEmpty())
    {
        IFileManager::Get().Delete(*EditorBridgeStatePath, false, true, true);
    }
}

bool FBlueprintToCodeExporterModule::TickEditorBridge(float DeltaTime)
{
    (void)DeltaTime;
    WriteEditorStateSnapshot(false);
    return true;
}

bool FBlueprintToCodeExporterModule::BuildEditorStateSnapshot(
    TSharedRef<FJsonObject>& OutSnapshot,
    FString& OutSemanticState
) const
{
    TSharedRef<FJsonObject> Snapshot = MakeShared<FJsonObject>();
    Snapshot->SetStringField(TEXT("schema"), SnapshotSchema);
    Snapshot->SetStringField(TEXT("protocolVersion"), SnapshotProtocol);
    Snapshot->SetStringField(TEXT("bridgeVersion"), EditorBridgeVersion);
    Snapshot->SetStringField(TEXT("bridgeInstanceId"), EditorBridgeInstanceId.ToString());
    const FString EngineVersion = FEngineVersion::Current().ToString();
    const FString AppBuildVersion = FApp::GetBuildVersion();
    Snapshot->SetStringField(TEXT("engineVersion"), EngineVersion);
    Snapshot->SetStringField(
        TEXT("buildVersion"),
        AppBuildVersion.IsEmpty() ? EngineVersion : AppBuildVersion
    );
    Snapshot->SetBoolField(TEXT("connected"), true);
    Snapshot->SetStringField(TEXT("reasonCode"), TEXT(""));

    const TArray<FString> Capabilities = {
        TEXT("READ_ACTIVE_ASSET"),
        TEXT("READ_ACTIVE_GRAPH"),
        TEXT("READ_GRAPH_POSITIONS"),
        TEXT("READ_DIRTY_STATE"),
        TEXT("READ_COMPILE_STATE")
    };
    Snapshot->SetArrayField(TEXT("capabilities"), StringValues(Capabilities));
    Snapshot->SetStringField(TEXT("selectionStatus"), TEXT("UNSUPPORTED_BY_DEVKIT_BUILD"));
    Snapshot->SetArrayField(
        TEXT("selectedNodeGuids"),
        TArray<TSharedPtr<FJsonValue>>()
    );

    FString ActivityStatus;
    UBlueprint* Blueprint = ResolveActiveBlueprint(ActivityStatus);
    Snapshot->SetStringField(TEXT("activityStatus"), ActivityStatus);
    if (!Blueprint)
    {
        Snapshot->SetStringField(TEXT("graphStatus"), TEXT("NO_ACTIVE_BLUEPRINT"));
        Snapshot->SetField(TEXT("activeBlueprint"), MakeShared<FJsonValueNull>());
        Snapshot->SetField(TEXT("focusedGraph"), MakeShared<FJsonValueNull>());
        OutSnapshot = Snapshot;
        OutSemanticState = JsonObjectToCompactString(Snapshot);
        return true;
    }

    TSharedRef<FJsonObject> ActiveBlueprint = MakeShared<FJsonObject>();
    ActiveBlueprint->SetStringField(TEXT("name"), Blueprint->GetName());
    ActiveBlueprint->SetStringField(TEXT("objectPath"), Blueprint->GetPathName());
    ActiveBlueprint->SetBoolField(
        TEXT("dirty"),
        Blueprint->GetOutermost()->IsDirty()
    );
    ActiveBlueprint->SetStringField(TEXT("compileStatus"), CompileStatus(Blueprint));
    Snapshot->SetObjectField(TEXT("activeBlueprint"), ActiveBlueprint);

    const TSharedPtr<IBlueprintEditor> BlueprintEditor =
        FKismetEditorUtilities::GetIBlueprintEditorForObject(Blueprint, false);
    if (!BlueprintEditor.IsValid())
    {
        Snapshot->SetStringField(
            TEXT("graphStatus"),
            TEXT("BLUEPRINT_EDITOR_INTERFACE_UNAVAILABLE")
        );
        Snapshot->SetField(TEXT("focusedGraph"), MakeShared<FJsonValueNull>());
        OutSnapshot = Snapshot;
        OutSemanticState = JsonObjectToCompactString(Snapshot);
        return true;
    }

    UEdGraph* FocusedGraph = BlueprintEditor->GetFocusedGraph();
    if (!FocusedGraph)
    {
        Snapshot->SetStringField(TEXT("graphStatus"), TEXT("NO_FOCUSED_GRAPH"));
        Snapshot->SetField(TEXT("focusedGraph"), MakeShared<FJsonValueNull>());
        OutSnapshot = Snapshot;
        OutSemanticState = JsonObjectToCompactString(Snapshot);
        return true;
    }

    TArray<FEditorNodeSnapshot> SortedNodes;
    SortedNodes.Reserve(FocusedGraph->Nodes.Num());
    for (UEdGraphNode* Node : FocusedGraph->Nodes)
    {
        if (!Node)
        {
            continue;
        }
        FEditorNodeSnapshot& NodeSnapshot = SortedNodes.AddDefaulted_GetRef();
        NodeSnapshot.Node = Node;
        NodeSnapshot.NodeGuid = Node->NodeGuid.ToString();
    }
    SortedNodes.Sort([](const FEditorNodeSnapshot& Left, const FEditorNodeSnapshot& Right)
    {
        if (Left.Node->NodePosY != Right.Node->NodePosY)
        {
            return Left.Node->NodePosY < Right.Node->NodePosY;
        }
        if (Left.Node->NodePosX != Right.Node->NodePosX)
        {
            return Left.Node->NodePosX < Right.Node->NodePosX;
        }
        if (Left.NodeGuid != Right.NodeGuid)
        {
            return Left.NodeGuid < Right.NodeGuid;
        }
        return Left.Node->GetName() < Right.Node->GetName();
    });

    TArray<TSharedPtr<FJsonValue>> NodeValues;
    const int32 ReturnedNodes = FMath::Min(SortedNodes.Num(), MaxSnapshotNodes);
    NodeValues.Reserve(ReturnedNodes);
    for (int32 Index = 0; Index < ReturnedNodes; ++Index)
    {
        const FEditorNodeSnapshot& Source = SortedNodes[Index];
        TSharedRef<FJsonObject> Node = MakeShared<FJsonObject>();
        Node->SetStringField(TEXT("nodeGuid"), Source.NodeGuid);
        Node->SetStringField(TEXT("name"), Source.Node->GetName());
        Node->SetStringField(TEXT("className"), Source.Node->GetClass()->GetName());
        Node->SetNumberField(TEXT("x"), Source.Node->NodePosX);
        Node->SetNumberField(TEXT("y"), Source.Node->NodePosY);
        NodeValues.Add(MakeShared<FJsonValueObject>(Node));
    }

    const UEdGraphSchema* Schema = FocusedGraph->GetSchema();
    TSharedRef<FJsonObject> Graph = MakeShared<FJsonObject>();
    Graph->SetStringField(TEXT("name"), FocusedGraph->GetName());
    Graph->SetStringField(TEXT("pathName"), FocusedGraph->GetPathName());
    Graph->SetStringField(
        TEXT("schemaClass"),
        Schema ? Schema->GetClass()->GetName() : TEXT("UNKNOWN")
    );
    Graph->SetStringField(TEXT("graphType"), ClassifyGraph(Blueprint, FocusedGraph));
    Graph->SetNumberField(TEXT("nodeCount"), SortedNodes.Num());
    Graph->SetBoolField(TEXT("nodesTruncated"), SortedNodes.Num() > ReturnedNodes);
    Graph->SetNumberField(TEXT("nodesOmitted"), SortedNodes.Num() - ReturnedNodes);
    Graph->SetArrayField(TEXT("nodes"), NodeValues);
    Snapshot->SetStringField(TEXT("graphStatus"), TEXT("FOCUSED_GRAPH"));
    Snapshot->SetObjectField(TEXT("focusedGraph"), Graph);

    OutSnapshot = Snapshot;
    OutSemanticState = JsonObjectToCompactString(Snapshot);
    return true;
}

bool FBlueprintToCodeExporterModule::WriteEditorStateSnapshot(bool bForce)
{
    TSharedRef<FJsonObject> Snapshot = MakeShared<FJsonObject>();
    FString SemanticState;
    if (!BuildEditorStateSnapshot(Snapshot, SemanticState))
    {
        return false;
    }

    const double NowSeconds = FPlatformTime::Seconds();
    const bool bSemanticChanged = SemanticState != LastBridgeSemanticState;
    const bool bHeartbeatDue =
        LastBridgeWriteSeconds <= 0.0
        || (NowSeconds - LastBridgeWriteSeconds) >= EditorBridgeHeartbeatSeconds;
    if (!bForce && !bSemanticChanged && !bHeartbeatDue)
    {
        return true;
    }

    ++EditorBridgeSequence;
    Snapshot->SetNumberField(TEXT("sequence"), static_cast<double>(EditorBridgeSequence));
    Snapshot->SetStringField(TEXT("writtenAtUtc"), FDateTime::UtcNow().ToIso8601());

    const FString StateDirectory = FPaths::GetPath(EditorBridgeStatePath);
    if (!IFileManager::Get().MakeDirectory(*StateDirectory, true))
    {
        UE_LOG(
            LogBlueprintToCodeEditorBridge,
            Warning,
            TEXT("Could not create the Editor bridge state directory.")
        );
        return false;
    }

    const FString TemporaryPath = FPaths::Combine(
        StateDirectory,
        FString::Printf(
            TEXT(".editor_state.json.%s.tmp"),
            *FGuid::NewGuid().ToString()
        )
    );
    const FString Serialized = JsonObjectToCompactString(Snapshot);
    const bool bSaved = FFileHelper::SaveStringToFile(
        Serialized,
        *TemporaryPath,
        FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM
    );
    const bool bReplaced = bSaved && AtomicReplaceFile(
        EditorBridgeStatePath,
        TemporaryPath
    );
    if (!bReplaced)
    {
        IFileManager::Get().Delete(*TemporaryPath, false, true, true);
        UE_LOG(
            LogBlueprintToCodeEditorBridge,
            Warning,
            TEXT("Could not atomically replace the Editor bridge snapshot.")
        );
        return false;
    }

    LastBridgeSemanticState = MoveTemp(SemanticState);
    LastBridgeWriteSeconds = NowSeconds;
    return true;
}

void FBlueprintToCodeExporterModule::ShowEditorBridgeStatus(const FToolMenuContext& MenuContext)
{
    (void)MenuContext;
    const bool bSnapshotExists = IFileManager::Get().FileExists(*EditorBridgeStatePath);
    const FString Message = bSnapshotExists
        ? TEXT("The read-only Editor state snapshot is active.")
        : TEXT("No Editor state snapshot exists yet. Check the Output Log.");
    FNotificationInfo Notification(FText::FromString(Message));
    Notification.ExpireDuration = 6.0f;
    Notification.bUseLargeFont = false;
    FSlateNotificationManager::Get().AddNotification(Notification);
}

void FBlueprintToCodeExporterModule::WriteEditorStateSnapshotNow(const FToolMenuContext& MenuContext)
{
    (void)MenuContext;
    const bool bWritten = WriteEditorStateSnapshot(true);
    const FString Message = bWritten
        ? TEXT("Wrote the read-only Editor state snapshot.")
        : TEXT("Could not write the Editor state snapshot. Check the Output Log.");
    FNotificationInfo Notification(FText::FromString(Message));
    Notification.ExpireDuration = 6.0f;
    Notification.bUseLargeFont = false;
    FSlateNotificationManager::Get().AddNotification(Notification);
}
