"""Exact Registry classification rules for the ARK asset taxonomy.

This module owns the closed technical allowlist, ancestry anchors, and the
canonical ruleset summary used in published manifests.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable


RULESET_VERSION = "ark-asset-taxonomy-rules/v1"


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


EXACT_TECHNICAL_RULES: dict[str, tuple[str, str, str]] = {
    "/Script/Engine.Blueprint": ("LOGIC", "BLUEPRINT", "exact.blueprint.v1"),
    "/Script/Engine.MacroLibrary": ("LOGIC", "MACRO_LIBRARY", "exact.macro-library.v1"),
    "/Script/AnimGraph.AnimBlueprint": (
        "ANIMATION",
        "ANIMATION_BLUEPRINT",
        "exact.animation-blueprint.v1",
    ),
    "/Script/UMGEditor.WidgetBlueprint": (
        "UI",
        "WIDGET_BLUEPRINT",
        "exact.widget-blueprint.v1",
    ),
    "/Script/UMG.WidgetBlueprint": (
        "UI",
        "WIDGET_BLUEPRINT",
        "exact.widget-blueprint-runtime.v1",
    ),
    "/Script/Engine.DataTable": ("DATA", "DATA_TABLE", "exact.data-table.v1"),
    "/Script/Engine.CurveTable": ("DATA", "CURVE_TABLE", "exact.curve-table.v1"),
    "/Script/Engine.DataAsset": ("DATA", "DATA_ASSET", "exact.data-asset.v1"),
    "/Script/Engine.PrimaryDataAsset": (
        "DATA",
        "PRIMARY_DATA_ASSET",
        "exact.primary-data-asset.v1",
    ),
    "/Script/Engine.StringTable": ("DATA", "STRING_TABLE", "exact.string-table.v1"),
    "/Script/Engine.World": ("WORLD", "WORLD", "exact.world.v1"),
    "/Script/Engine.MapBuildDataRegistry": (
        "WORLD",
        "MAP_BUILD_DATA",
        "exact.map-build-data.v1",
    ),
    "/Script/Engine.Texture2D": ("VISUAL", "TEXTURE_2D", "exact.texture2d.v1"),
    "/Script/Engine.TextureCube": ("VISUAL", "TEXTURE_CUBE", "exact.texture-cube.v1"),
    "/Script/Engine.TextureRenderTarget2D": (
        "VISUAL",
        "RENDER_TARGET_2D",
        "exact.render-target2d.v1",
    ),
    "/Script/Engine.Material": ("VISUAL", "MATERIAL", "exact.material.v1"),
    "/Script/Engine.MaterialInstanceConstant": (
        "VISUAL",
        "MATERIAL_INSTANCE",
        "exact.material-instance.v1",
    ),
    "/Script/Engine.MaterialFunction": (
        "VISUAL",
        "MATERIAL_FUNCTION",
        "exact.material-function.v1",
    ),
    "/Script/Engine.MaterialFunctionMaterialLayer": (
        "VISUAL",
        "MATERIAL_LAYER",
        "exact.material-layer.v1",
    ),
    "/Script/Engine.MaterialFunctionMaterialLayerBlend": (
        "VISUAL",
        "MATERIAL_LAYER_BLEND",
        "exact.material-layer-blend.v1",
    ),
    "/Script/Engine.StaticMesh": ("VISUAL", "STATIC_MESH", "exact.static-mesh.v1"),
    "/Script/Engine.SkeletalMesh": (
        "VISUAL",
        "SKELETAL_MESH",
        "exact.skeletal-mesh.v1",
    ),
    "/Script/Engine.PhysicsAsset": (
        "VISUAL",
        "PHYSICS_ASSET",
        "exact.physics-asset.v1",
    ),
    "/Script/Engine.Font": ("UI", "FONT", "exact.font.v1"),
    "/Script/Engine.FontFace": ("UI", "FONT_FACE", "exact.font-face.v1"),
    "/Script/Engine.SoundWave": ("AUDIO", "SOUND_WAVE", "exact.sound-wave.v1"),
    "/Script/Engine.SoundCue": ("AUDIO", "SOUND_CUE", "exact.sound-cue.v1"),
    "/Script/Engine.SoundClass": ("AUDIO", "SOUND_CLASS", "exact.sound-class.v1"),
    "/Script/Engine.SoundMix": ("AUDIO", "SOUND_MIX", "exact.sound-mix.v1"),
    "/Script/Engine.DialogueWave": (
        "AUDIO",
        "DIALOGUE_WAVE",
        "exact.dialogue-wave.v1",
    ),
    "/Script/Engine.AnimSequence": (
        "ANIMATION",
        "ANIMATION_SEQUENCE",
        "exact.anim-sequence.v1",
    ),
    "/Script/Engine.AnimMontage": (
        "ANIMATION",
        "ANIMATION_MONTAGE",
        "exact.anim-montage.v1",
    ),
    "/Script/Engine.BlendSpace": (
        "ANIMATION",
        "BLEND_SPACE",
        "exact.blend-space.v1",
    ),
    "/Script/Engine.AimOffsetBlendSpace": (
        "ANIMATION",
        "AIM_OFFSET",
        "exact.aim-offset.v1",
    ),
    "/Script/Engine.Skeleton": ("ANIMATION", "SKELETON", "exact.skeleton.v1"),
    "/Script/Engine.PoseAsset": ("ANIMATION", "POSE_ASSET", "exact.pose-asset.v1"),
    "/Script/LevelSequence.LevelSequence": (
        "ANIMATION",
        "LEVEL_SEQUENCE",
        "exact.level-sequence.v1",
    ),
    "/Script/Niagara.NiagaraSystem": (
        "VFX",
        "NIAGARA_SYSTEM",
        "exact.niagara-system.v1",
    ),
    "/Script/Niagara.NiagaraEmitter": (
        "VFX",
        "NIAGARA_EMITTER",
        "exact.niagara-emitter.v1",
    ),
    "/Script/Engine.ParticleSystem": (
        "VFX",
        "PARTICLE_SYSTEM",
        "exact.particle-system.v1",
    ),
    "/Script/Engine.UserDefinedStruct": (
        "SCHEMA",
        "USER_DEFINED_STRUCT",
        "exact.user-struct.v1",
    ),
    "/Script/Engine.UserDefinedEnum": (
        "SCHEMA",
        "USER_DEFINED_ENUM",
        "exact.user-enum.v1",
    ),
    "/Script/CoreUObject.ObjectRedirector": (
        "REDIRECTOR",
        "OBJECT_REDIRECTOR",
        "exact.redirector.v1",
    ),
}


def _register_exact_group(
    family: str,
    kind: str,
    rule_id: str,
    script_classes: Iterable[str],
) -> None:
    for script_class in script_classes:
        class_path = f"/Script/{script_class}"
        EXACT_TECHNICAL_RULES.setdefault(class_path, (family, kind, rule_id))


_register_exact_group(
    "WORLD",
    "ACTOR",
    "exact.world-actor.v1",
    (
        "Engine.StaticMeshActor",
        "Engine.SkeletalMeshActor",
        "Engine.PlayerStart",
        "Engine.DecalActor",
        "Engine.TextRenderActor",
        "Engine.TargetPoint",
    ),
)
_register_exact_group(
    "WORLD",
    "LEVEL_WORLD",
    "exact.world-level.v1",
    ("Engine.World", "Engine.LevelInstance"),
)
_register_exact_group(
    "WORLD",
    "WORLD_PARTITION",
    "exact.world-partition.v1",
    (
        "Engine.WorldPartitionHLOD",
        "Engine.WorldDataLayers",
        "Engine.WorldPartitionMiniMap",
        "Engine.DataLayerAsset",
        "Engine.HLODLayer",
        "Engine.MapBuildDataRegistry",
        "Engine.LevelBounds",
    ),
)
_register_exact_group(
    "WORLD",
    "FOLIAGE",
    "exact.world-foliage.v1",
    (
        "Foliage.InstancedFoliageActor",
        "Foliage.FoliageType_InstancedStaticMesh",
        "Foliage.FoliageType_Actor",
        "Engine.FoliageType_InstancedStaticMesh",
    ),
)
_register_exact_group(
    "WORLD",
    "LANDSCAPE",
    "exact.world-landscape.v1",
    (
        "Landscape.LandscapeStreamingProxy",
        "Landscape.Landscape",
        "Landscape.LandscapeGrassType",
        "Landscape.LandscapeLayerInfoObject",
        "Engine.LandscapeLayerInfoObject",
    ),
)
_register_exact_group(
    "WORLD",
    "VOLUME",
    "exact.world-volume.v1",
    (
        "Engine.PainCausingVolume",
        "Engine.PostProcessVolume",
        "Engine.BlockingVolume",
        "Engine.PhysicsVolume",
        "Engine.TileStreamingVolume",
        "Engine.TriggerVolume",
        "Engine.KillZVolume",
        "Engine.LocationVolume",
    ),
)
_register_exact_group(
    "WORLD",
    "LIGHTING_ATMOSPHERE",
    "exact.world-lighting.v1",
    (
        "Engine.PointLight",
        "Engine.SpotLight",
        "Engine.RectLight",
        "Engine.SkyLight",
        "Engine.DirectionalLight",
        "Engine.LocalSkyLightOverride",
        "Engine.ExponentialHeightFog",
        "Engine.AtmosphericFog",
        "Engine.SkyAtmosphere",
        "Engine.VolumetricCloud",
        "Engine.WindDirectionalSource",
    ),
)
_register_exact_group(
    "WORLD",
    "NAVIGATION",
    "exact.world-navigation.v1",
    ("NavigationSystem.NavModifierVolume", "NavigationSystem.NavMeshBoundsVolume"),
)
_register_exact_group(
    "WORLD",
    "VIRTUAL_TEXTURE_WORLD",
    "exact.world-virtual-texture.v1",
    ("Engine.RuntimeVirtualTextureVolume", "Engine.VirtualTextureBuilder"),
)
_register_exact_group(
    "VISUAL",
    "TEXTURE",
    "exact.visual-texture.v1",
    (
        "Engine.Texture2D",
        "Engine.Texture2DArray",
        "Engine.TextureCube",
        "Engine.TextureRenderTarget2D",
        "Engine.TextureRenderTargetCube",
        "Engine.TextureRenderTargetVolume",
        "Engine.VolumeTexture",
        "Engine.TextureLightProfile",
        "Engine.RuntimeVirtualTexture",
        "Engine.CanvasRenderTarget2D",
        "Engine.StaticSparseVolumeTexture",
        "VirtualHeightfieldMesh.HeightfieldMinMaxTexture",
    ),
)
_register_exact_group(
    "VISUAL",
    "MATERIAL",
    "exact.visual-material.v1",
    (
        "Engine.Material",
        "Engine.MaterialInstanceConstant",
        "Engine.LandscapeMaterialInstanceConstant",
        "Engine.MaterialFunction",
        "Engine.MaterialFunctionInstance",
        "Engine.MaterialFunctionMaterialLayer",
        "Engine.MaterialFunctionMaterialLayerInstance",
        "Engine.MaterialFunctionMaterialLayerBlend",
        "Engine.MaterialFunctionMaterialLayerBlendInstance",
        "Engine.MaterialParameterCollection",
        "Engine.SubsurfaceProfile",
    ),
)
_register_exact_group(
    "VISUAL",
    "GEOMETRY",
    "exact.visual-geometry.v1",
    (
        "Engine.StaticMesh",
        "Engine.SkeletalMesh",
        "Engine.DestructibleMesh",
        "ApexDestruction.DestructibleMesh",
        "GeometryCollectionEngine.GeometryCollection",
        "GeometryCache.GeometryCache",
        "HairStrandsCore.GroomAsset",
    ),
)
_register_exact_group(
    "VISUAL",
    "VISUAL_CONFIG",
    "exact.visual-config.v1",
    (
        "Engine.SkeletalMeshLODSettings",
        "LODRecipe.LODRecipe",
        "OpenColorIO.OpenColorIOConfiguration",
    ),
)
_register_exact_group(
    "AUDIO",
    "SOURCE",
    "exact.audio-source.v1",
    (
        "Engine.SoundWave",
        "Engine.SoundCue",
        "Engine.DialogueVoice",
        "Engine.DialogueWave",
        "MetasoundEngine.MetaSoundSource",
    ),
)
_register_exact_group(
    "AUDIO",
    "PLACEMENT",
    "exact.audio-placement.v1",
    ("Engine.AmbientSound", "Engine.AudioVolume"),
)
_register_exact_group(
    "AUDIO",
    "CONFIG_EFFECT",
    "exact.audio-config-effect.v1",
    (
        "Engine.SoundClass",
        "Engine.SoundAttenuation",
        "Engine.SoundMix",
        "Engine.SoundSubmix",
        "Engine.SoundConcurrency",
        "Engine.EndpointSubmix",
        "Engine.ReverbEffect",
        "Synthesis.AudioImpulseResponse",
        "Synthesis.SubmixEffectConvolutionReverbPreset",
        "AudioMixer.SubmixEffectReverbPreset",
        "AudioMixer.SubmixEffectSubmixEQPreset",
        "ResonanceAudio.ResonanceAudioReverbPluginPreset",
    ),
)
_register_exact_group(
    "ANIMATION",
    "CLIP_BLEND",
    "exact.animation-clip-blend.v1",
    (
        "Engine.AnimSequence",
        "Engine.AnimMontage",
        "Engine.AnimComposite",
        "Engine.BlendSpace",
        "Engine.BlendSpace1D",
        "Engine.AimOffsetBlendSpace",
        "Engine.AimOffsetBlendSpace1D",
    ),
)
_register_exact_group(
    "ANIMATION",
    "BLUEPRINT_RIG",
    "exact.animation-blueprint-rig.v1",
    (
        "Engine.AnimBlueprint",
        "Engine.Skeleton",
        "Engine.PoseAsset",
        "Engine.Rig",
        "ControlRigDeveloper.ControlRigBlueprint",
        "IKRig.IKRigDefinition",
        "IKRig.IKRetargeter",
        "ControlRig.ControlRigShapeLibrary",
        "AnimationSharing.AnimationSharingSetup",
        "OptimusCore.OptimusDeformer",
        "OptimusCore.OptimusSource",
    ),
)
_register_exact_group(
    "ANIMATION",
    "SETTINGS",
    "exact.animation-settings.v1",
    (
        "Engine.AnimBoneCompressionSettings",
        "Engine.AnimCurveCompressionSettings",
        "Engine.VariableFrameStrippingSettings",
    ),
)
_register_exact_group(
    "ANIMATION",
    "BAKE_DATA",
    "exact.animation-bake.v1",
    ("AnimToTexture.AnimToTextureDataAsset",),
)
_register_exact_group(
    "PHYSICS",
    "PHYSICS_ASSET",
    "exact.physics-asset-group.v1",
    ("Engine.PhysicsAsset", "Engine.PhysicalMaterial", "PhysicsCore.PhysicalMaterial"),
)
_register_exact_group(
    "VFX",
    "SYSTEM",
    "exact.vfx-system.v1",
    (
        "Engine.ParticleSystem",
        "Engine.Emitter",
        "Engine.SubUVAnimation",
        "Engine.VectorFieldStatic",
        "Niagara.NiagaraActor",
        "Niagara.NiagaraSystem",
        "Niagara.NiagaraScript",
        "Niagara.NiagaraEffectType",
        "Niagara.NiagaraEmitter",
        "Niagara.NiagaraParameterCollection",
        "Niagara.NiagaraDataChannelAsset",
        "Niagara.NiagaraAssetTagDefinitions",
        "Niagara.NiagaraValidationRuleSet",
    ),
)
_register_exact_group(
    "UI",
    "UI_ASSET",
    "exact.ui-asset.v1",
    (
        "UMGEditor.WidgetBlueprint",
        "Engine.Font",
        "Engine.FontFace",
        "SlateCore.SlateWidgetStyleAsset",
    ),
)
_register_exact_group(
    "DATA",
    "TABLE_CURVE",
    "exact.data-table-curve.v1",
    (
        "Engine.DataTable",
        "Engine.CompositeDataTable",
        "Engine.MirrorDataTable",
        "Engine.CurveFloat",
        "Engine.CurveVector",
        "Engine.CurveLinearColor",
        "Engine.CurveLinearColorAtlas",
    ),
)
_register_exact_group(
    "DATA",
    "ASSET_MANAGEMENT",
    "exact.data-asset-management.v1",
    ("Engine.PrimaryAssetLabel",),
)
_register_exact_group(
    "SCHEMA",
    "TYPE",
    "exact.schema-type.v1",
    (
        "Engine.UserDefinedEnum",
        "Engine.UserDefinedStruct",
        "CoreUObject.UserDefinedStruct",
    ),
)
_register_exact_group(
    "AI",
    "GRAPH",
    "exact.ai-graph.v1",
    ("AIModule.BehaviorTree", "AIModule.BlackboardData", "StateTreeModule.StateTree"),
)
_register_exact_group(
    "CINEMATIC_MEDIA",
    "ASSET",
    "exact.cinematic-media.v1",
    (
        "LevelSequence.LevelSequence",
        "LevelSequence.LevelSequenceActor",
        "TemplateSequence.TemplateSequence",
        "TemplateSequence.CameraAnimationSequence",
        "DaySequence.DaySequence",
        "DaySequence.DaySequenceCollectionAsset",
        "Engine.InterpData",
        "Engine.CameraActor",
        "Engine.CameraAnim",
        "CinematicCamera.CineCameraActor",
        "BinkMediaPlayer.BinkMediaPlayer",
        "BinkMediaPlayer.BinkMediaTexture",
        "MediaAssets.MediaTexture",
        "MediaAssets.MediaPlayer",
        "MediaAssets.FileMediaSource",
        "MediaAssets.StreamMediaSource",
        "ImgMedia.ImgMediaSource",
        "MovieRenderPipelineCore.MoviePipelineMasterConfig",
        "MovieRenderPipelineCore.MovieGraphConfig",
    ),
)
_register_exact_group(
    "INPUT",
    "INPUT_ASSET",
    "exact.input-asset.v1",
    (
        "EnhancedInput.InputAction",
        "EnhancedInput.InputMappingContext",
        "Engine.TouchInterface",
        "Engine.ForceFeedbackEffect",
    ),
)
_register_exact_group(
    "PROCEDURAL",
    "GRAPH",
    "exact.procedural-graph.v1",
    (
        "PCG.PCGGraph",
        "PCG.PCGWorldActor",
        "PCG.PCGBlueprintSettings",
        "HoudiniEngineRuntime.HoudiniAsset",
        "DataflowEngine.Dataflow",
    ),
)
_register_exact_group(
    "ML",
    "MODEL",
    "exact.ml-model.v1",
    (
        "NNE.NNEModelData",
        "NNEDenoiser.NNEDenoiserAsset",
        "NNEDenoiser.NNEDenoiserTemporalAsset",
    ),
)
_register_exact_group(
    "EDITOR",
    "TOOL_METADATA",
    "exact.editor-tool-metadata.v1",
    (
        "Blutility.EditorUtilityBlueprint",
        "Blutility.EditorUtilityWidgetBlueprint",
        "Engine.ActorFolder",
        "Engine.Note",
        "UnrealEd.GroupActor",
        "FoliageEdit.FoliageSelectOverrides",
        "NiagaraEditor.NiagaraParameterDefinitions",
        "InterchangePipelines.InterchangeGenericAssetsPipeline",
        "InterchangePipelines.InterchangeGLTFPipeline",
        "InterchangePipelines.InterchangeGenericLevelPipeline",
        "InterchangePipelines.InterchangeGenericMaterialPipeline",
        "InterchangePipelines.InterchangeGenericTexturePipeline",
        "InterchangePipelines.InterchangeMaterialXPipeline",
        "InterchangeEditorPipelines.InterchangeGraphInspectorPipeline",
        "ConcertSyncClient.ConcertAssetContainer",
        "VREditor.VREditorAssetContainer",
        "ViewportInteraction.ViewportInteractionAssetContainer",
    ),
)


BLUEPRINT_TYPE_RULES: dict[str, tuple[str, str, str]] = {
    "BPTYPE_FunctionLibrary": (
        "LOGIC",
        "FUNCTION_LIBRARY",
        "tag.blueprint-type-function-library.v1",
    ),
    "BPTYPE_Interface": (
        "SCHEMA",
        "BLUEPRINT_INTERFACE",
        "tag.blueprint-type-interface.v1",
    ),
    "BPTYPE_MacroLibrary": (
        "LOGIC",
        "MACRO_LIBRARY",
        "tag.blueprint-type-macro-library.v1",
    ),
}


TECHNICAL_ANCESTRY_RULES: tuple[tuple[str, str, str], ...] = (
    ("/Script/Engine.DataAsset", "DATA", "DATA_ASSET_SUBCLASS"),
    ("/Script/Engine.Texture", "VISUAL", "TEXTURE_SUBCLASS"),
    ("/Script/Engine.MaterialInterface", "VISUAL", "MATERIAL_SUBCLASS"),
    ("/Script/Engine.StaticMesh", "VISUAL", "STATIC_MESH_SUBCLASS"),
    ("/Script/Engine.SkeletalMesh", "VISUAL", "SKELETAL_MESH_SUBCLASS"),
    ("/Script/Engine.SoundBase", "AUDIO", "SOUND_SUBCLASS"),
    ("/Script/Engine.AnimationAsset", "ANIMATION", "ANIMATION_ASSET_SUBCLASS"),
    ("/Script/Niagara.NiagaraSystem", "VFX", "NIAGARA_SYSTEM_SUBCLASS"),
    ("/Script/Engine.World", "WORLD", "WORLD_SUBCLASS"),
)


SEMANTIC_ROOTS: tuple[tuple[str, str], ...] = (
    ("GAMEPLAY_BUFF", "/Script/ShooterGame.PrimalBuff"),
    ("GAMEPLAY_ITEM", "/Script/ShooterGame.PrimalItem"),
    ("GAMEPLAY_INVENTORY", "/Script/ShooterGame.PrimalInventoryComponent"),
    (
        "CHARACTER_STATUS",
        "/Script/ShooterGame.PrimalCharacterStatusComponent",
    ),
    ("CREATURE", "/Script/ShooterGame.PrimalDinoCharacter"),
    ("STRUCTURE", "/Script/ShooterGame.PrimalStructure"),
    ("WEAPON", "/Script/ShooterGame.ShooterWeapon"),
    ("DAMAGE_TYPE", "/Script/Engine.DamageType"),
    ("ACTOR_COMPONENT", "/Script/Engine.ActorComponent"),
    ("DATA_ASSET", "/Script/Engine.DataAsset"),
    ("USER_INTERFACE", "/Script/UMG.UserWidget"),
    ("GAME_MODE", "/Script/Engine.GameModeBase"),
)


# Path/name tokens are recall aids only. They are intentionally separate from
# confirmed class-root semantics and are included in the ruleset digest.
SEMANTIC_CANDIDATE_TOKEN_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("BUFF_ASSOCIATED_CONTENT", ("buff", "buffs")),
    ("ITEM_ASSOCIATED_CONTENT", ("item", "items")),
    ("WEAPON_ASSOCIATED_CONTENT", ("weapon", "weapons")),
    ("CREATURE_ASSOCIATED_CONTENT", ("creature", "creatures", "dino", "dinos")),
    ("STRUCTURE_ASSOCIATED_CONTENT", ("structure", "structures")),
    ("UI_ASSOCIATED_CONTENT", ("ui",)),
)


def _ruleset_digest() -> str:
    payload = {
        "version": RULESET_VERSION,
        "exactTechnical": sorted(
            (key, *value) for key, value in EXACT_TECHNICAL_RULES.items()
        ),
        "blueprintTypes": sorted(
            (key, *value) for key, value in BLUEPRINT_TYPE_RULES.items()
        ),
        "technicalAncestry": TECHNICAL_ANCESTRY_RULES,
        "semanticRoots": SEMANTIC_ROOTS,
        "semanticCandidateTokenRecall": SEMANTIC_CANDIDATE_TOKEN_RULES,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _ruleset_summary() -> dict[str, object]:
    return {
        "version": RULESET_VERSION,
        "sha256": _ruleset_digest(),
        "exactClassRuleCount": len(EXACT_TECHNICAL_RULES),
        "semanticCandidateRuleCount": len(SEMANTIC_CANDIDATE_TOKEN_RULES),
        "nameOrFolderConfirmedSemanticInference": False,
        "nameOrFolderCandidateRecall": True,
    }
