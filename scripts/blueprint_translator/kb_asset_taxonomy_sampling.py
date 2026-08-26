"""Deterministic multidimensional sampling for ARK taxonomy packages."""

from __future__ import annotations

import hashlib
import heapq
import json
import math
from collections import Counter
from collections.abc import Mapping
from pathlib import Path


SAMPLE_MANIFEST_SCHEMA = "ark.kb.asset-taxonomy-sample.v1"
SAMPLE_ALGORITHM_VERSION = "quota-first-weighted-greedy-coverage/v5"
SAMPLER_POLICY_VERSION = "ark-asset-taxonomy-sampler-policy/v2"
DEFAULT_TARGET_CANDIDATE_LIMIT = 3
QUOTA_CANDIDATE_MULTIPLIER = 4
QUOTA_CANDIDATE_MINIMUM = 32
QUOTA_REPAIR_MAX_ADD_CANDIDATES_PER_DEFICIT = 32
QUOTA_REPAIR_MAX_NEUTRAL_STATES = 256
QUOTA_TARGET_CANDIDATE_PREFIXES = (
    "samplingLayer=WORLD_PARTITION_EXTERNAL_",
    "classificationConfidence=UNKNOWN",
    "technicalFamily=",
    "technicalKind=",
    "confirmedSemanticRole=",
    "candidateSemanticRole=",
    "packageShape=",
    "mountPoint=",
    "originAreaTailBucket=",
    "unknownClassBucket=",
    "unknownClassProvenance=",
    "unknownPlacement=",
)
ENUMERATED_CATEGORY_QUOTA_PREFIXES = (
    "technicalKind=",
    "candidateSemanticRole=",
    "packageShape=",
    "mountPoint=",
    "originAreaCandidate=",
    "originAreaTailBucket=",
    "unknownClassBucket=",
)


def _unknown_class_provenance(class_path: str) -> str:
    if class_path.startswith("/Script/ShooterGame."):
        return "SHOOTERGAME_NATIVE_CLASS"
    if class_path.startswith("/Script/"):
        return "OTHER_NATIVE_CLASS"
    if class_path.startswith("/Game/") and class_path.endswith("_C"):
        return "GAME_GENERATED_CLASS"
    if class_path.startswith("/") and class_path.endswith("_C"):
        return "PLUGIN_GENERATED_CLASS"
    return "OTHER_CLASS_PATH"


def _unknown_class_paths(package: Mapping[str, object]) -> list[str]:
    return sorted(
        {
            str(item["assetClassPath"])
            for item in package["objects"]
            if item["classificationConfidence"] == "UNKNOWN"
        }
    )


def _sample_targets(package: Mapping[str, object]) -> list[str]:
    origin_area = str(package["originArea"])
    origin_bucket = hashlib.sha256(origin_area.encode("utf-8")).hexdigest()[0]
    targets = {
        f"technicalFamily={package['primaryTechnicalFamily']}",
        f"technicalKind={package['primaryTechnicalKind']}",
        f"sourceScope={package['sourceScope']}",
        f"mountPoint={package['mountPoint']}",
        f"layoutKind={package['layoutKind']}",
        f"packageShape={package['packageShape']}",
        f"classificationConfidence={package['classificationConfidence']}",
        f"semanticStatus={package['semanticStatus']}",
        f"samplingLayer={package['samplingLayer']}",
        f"originAreaCandidate={origin_area}",
        f"originAreaTailBucket={origin_bucket}",
    }
    for anchor in package["confirmedSemanticAnchors"]:
        targets.add(f"confirmedSemanticRole={anchor['role']}")
    for candidate in package["semanticCandidates"]:
        targets.add(f"candidateSemanticRole={candidate['role']}")
    for capability in package["availableCapabilities"]:
        targets.add(f"availableCapability={capability}")
    for route in package["acquisitionRoutes"]:
        targets.add(f"acquisitionRoute={route}")
    for capability, status in package["capabilityStatus"].items():
        targets.add(f"capabilityStatus.{capability}={status}")
    unknown_paths = _unknown_class_paths(package)
    if unknown_paths:
        targets.add(f"unknownPlacement={package['samplingLayer']}")
    for class_path in unknown_paths:
        provenance = _unknown_class_provenance(class_path)
        bucket = hashlib.sha256(class_path.encode("utf-8")).hexdigest()[0]
        targets.add(f"unknownClassProvenance={provenance}")
        targets.add(f"unknownClassBucket={bucket}")
        targets.add(f"unknownClassPath={class_path}")
    return sorted(targets)


_TARGET_WEIGHTS = {
    "confirmedSemanticRole": 8.0,
    "candidateSemanticRole": 3.0,
    "technicalFamily": 6.0,
    "unknownClassBucket": 6.0,
    "unknownClassPath": 7.0,
    "unknownClassProvenance": 6.0,
    "unknownPlacement": 6.0,
    "mountPoint": 5.0,
    "originAreaCandidate": 4.0,
    "originAreaTailBucket": 3.0,
    "layoutKind": 5.0,
    "samplingLayer": 5.0,
    "availableCapability": 4.0,
    "acquisitionRoute": 4.0,
    "technicalKind": 3.0,
    "sourceScope": 3.0,
    "semanticStatus": 2.0,
    "packageShape": 2.0,
    "classificationConfidence": 2.0,
    "capabilityStatus": 1.5,
}


def _sampler_policy_summary(sample_size: int) -> dict[str, object]:
    policy = {
        "version": SAMPLER_POLICY_VERSION,
        "algorithm": SAMPLE_ALGORITHM_VERSION,
        "targetWeights": _TARGET_WEIGHTS,
        "targetExtractionVersion": "multiaxis-registry-package-targets/v2",
        "targetHashPolicy": "sha256-first-hex-character/16-buckets",
        "populationWeightFormula": "baseWeight/(1+log2(population+1))",
        "candidateRankPolicy": "sha256(seed+NUL+packageName+NUL+profileFingerprint); ascending",
        "candidatePools": {
            "defaultPerTarget": DEFAULT_TARGET_CANDIDATE_LIMIT,
            "quotaMultiplier": QUOTA_CANDIDATE_MULTIPLIER,
            "quotaMinimum": QUOTA_CANDIDATE_MINIMUM,
            "quotaPerTarget": max(
                sample_size * QUOTA_CANDIDATE_MULTIPLIER,
                QUOTA_CANDIDATE_MINIMUM,
            ),
            "quotaTargetPrefixes": QUOTA_TARGET_CANDIDATE_PREFIXES,
            "inactiveDetailedTargetsExcludedFromCandidateUnion": True,
        },
        "coverageTargetPolicy": {
            "topOriginAreas": 12,
            "originAreaTailHashBuckets": 16,
            "topUnknownClassPaths": 6,
            "unknownClassHashBuckets": 16,
        },
        "quotas": {
            "populationQuotaPrefixes": (
                "technicalFamily=",
                "confirmedSemanticRole=",
            ),
            "enumeratedCategoryPrefixes": ENUMERATED_CATEGORY_QUOTA_PREFIXES,
            "populationFormula": {
                "population1To99": 1,
                "population100To9999": 2,
                "population10000OrMore": 3,
            },
            "unknownMinimum": "max(16,ceil(sampleSize*0.10)) when sampleSize>=32; otherwise max(3,ceil(sampleSize*0.10))",
            "worldPartitionActor": {"minimumRatio": 0.15, "maximumRatio": 0.20},
            "worldPartitionObject": {"minimumRatio": 0.03, "maximumRatio": 0.05},
        },
        "quotaSelectionPolicy": (
            "global greedy multicover by deficit-target count, scarcity weight, "
            "coverage weight, then candidate rank; deterministic one-for-one repair "
            "followed by a bounded depth-two neutral-state repair"
        ),
        "quotaRepairPolicy": {
            "maxDepth": 2,
            "maxAddCandidatesPerDeficit": QUOTA_REPAIR_MAX_ADD_CANDIDATES_PER_DEFICIT,
            "maxNeutralStates": QUOTA_REPAIR_MAX_NEUTRAL_STATES,
            "exhaustive": False,
            "acceptance": "final total shortfall must strictly decrease",
            "failureMeaning": (
                "DEGRADED means this bounded search did not meet every quota; "
                "it is not a proof that no feasible combination exists"
            ),
        },
        "statusPolicy": (
            "DEGRADED on size/quota shortfall; COVERAGE_PARTIAL on soft target gap; "
            "COMPLETE only when both are complete"
        ),
        "worldPartitionClusterKey": "samplingLayer+ownerWorldPathCandidate+actorClass",
        "worldPartitionMinimumPolicy": (
            "requested ratio minimum clamped to distinct cluster candidate capacity"
        ),
    }
    digest = hashlib.sha256(
        json.dumps(
            policy,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {**policy, "sha256": digest}


def _target_weight(target: str, population: int) -> float:
    dimension = target.split("=", 1)[0].split(".", 1)[0]
    return _TARGET_WEIGHTS.get(dimension, 1.0) / (1.0 + math.log2(population + 1))


def _candidate_rank(seed: str, package: Mapping[str, object]) -> str:
    payload = "\0".join(
        (seed, str(package["packageName"]), str(package["profileFingerprint"]))
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _keep_smallest(
    heap: list[tuple[int, str]],
    rank: str,
    package_name: str,
    limit: int,
) -> None:
    if limit <= 0:
        return
    numeric = int(rank, 16)
    entry = (-numeric, package_name)
    if len(heap) < limit:
        heapq.heappush(heap, entry)
    elif numeric < -heap[0][0]:
        heapq.heapreplace(heap, entry)


def _distribution(counter: Counter[str]) -> dict[str, int]:
    return {key: int(counter[key]) for key in sorted(counter)}


def _update_distributions(
    distributions: dict[str, Counter[str]],
    package: Mapping[str, object],
) -> None:
    direct = {
        "technicalFamily": package["primaryTechnicalFamily"],
        "technicalKind": package["primaryTechnicalKind"],
        "sourceScope": package["sourceScope"],
        "mountPoint": package["mountPoint"],
        "storageArea": package["storageArea"],
        "originArea": package["originArea"],
        "layoutKind": package["layoutKind"],
        "packageShape": package["packageShape"],
        "classificationConfidence": package["classificationConfidence"],
        "semanticStatus": package["semanticStatus"],
        "samplingLayer": package["samplingLayer"],
    }
    for dimension, value in direct.items():
        distributions[dimension][str(value)] += 1
    object_count = int(package["objectCount"])
    for dimension, value in {
        "assetObjectMountPoint": package["mountPoint"],
        "assetObjectSourceScope": package["sourceScope"],
        "assetObjectLayoutKind": package["layoutKind"],
    }.items():
        distributions[dimension][str(value)] += object_count
    for item in package["objects"]:
        distributions["assetObjectTechnicalFamily"][str(item["technicalFamily"])] += 1
        distributions["assetObjectTechnicalKind"][str(item["technicalKind"])] += 1
        distributions["assetObjectClassificationConfidence"][
            str(item["classificationConfidence"])
        ] += 1
    for anchor in package["confirmedSemanticAnchors"]:
        distributions["confirmedSemanticRole"][str(anchor["role"])] += 1
    for candidate in package["semanticCandidates"]:
        distributions["candidateSemanticRole"][str(candidate["role"])] += 1
    for capability in package["availableCapabilities"]:
        distributions["availableCapability"][str(capability)] += 1
    for route in package["acquisitionRoutes"]:
        distributions["acquisitionRoute"][str(route)] += 1


def _read_candidate_records(
    taxonomy_path: Path,
    candidate_names: set[str],
    *,
    seed: str,
    external_cluster_limit: int,
) -> tuple[dict[str, dict[str, object]], dict[str, int], dict[str, int]]:
    records: dict[str, dict[str, object]] = {}
    external_layers = {
        "WORLD_PARTITION_EXTERNAL_ACTOR",
        "WORLD_PARTITION_EXTERNAL_OBJECT",
    }
    cluster_candidates: dict[str, dict[str, tuple[int, str, dict[str, object]]]] = {
        layer: {} for layer in external_layers
    }
    cluster_heaps: dict[str, list[tuple[int, str, str]]] = {
        layer: [] for layer in external_layers
    }
    all_external_clusters: dict[str, set[str]] = {
        layer: set() for layer in external_layers
    }

    def retain_external_cluster_candidate(row: dict[str, object]) -> None:
        if external_cluster_limit <= 0:
            return
        layer = str(row["samplingLayer"])
        if layer not in external_layers:
            return
        cluster = str(row["sampleClusterKey"])
        all_external_clusters[layer].add(cluster)
        name = str(row["packageName"])
        numeric_rank = int(_candidate_rank(seed, row), 16)
        by_cluster = cluster_candidates[layer]
        heap = cluster_heaps[layer]
        current = by_cluster.get(cluster)
        if current is not None:
            if (numeric_rank, name) >= (current[0], current[1]):
                return
            by_cluster[cluster] = (numeric_rank, name, row)
            heapq.heappush(heap, (-numeric_rank, cluster, name))
            return
        if len(by_cluster) >= external_cluster_limit:
            while heap:
                negative_rank, worst_cluster, worst_name = heap[0]
                worst = by_cluster.get(worst_cluster)
                if worst is not None and (-negative_rank, worst_name) == (
                    worst[0],
                    worst[1],
                ):
                    break
                heapq.heappop(heap)
            if not heap:
                raise RuntimeError("EXTERNAL_CLUSTER_HEAP_EMPTY")
            negative_rank, worst_cluster, worst_name = heap[0]
            if (numeric_rank, name) >= (-negative_rank, worst_name):
                return
            heapq.heappop(heap)
            del by_cluster[worst_cluster]
        by_cluster[cluster] = (numeric_rank, name, row)
        heapq.heappush(heap, (-numeric_rank, cluster, name))

    with taxonomy_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            package_name = str(row["packageName"])
            if package_name in candidate_names:
                records[package_name] = row
            retain_external_cluster_candidate(row)
    for by_cluster in cluster_candidates.values():
        for _numeric_rank, package_name, row in by_cluster.values():
            records[package_name] = row
    return (
        records,
        {layer: len(clusters) for layer, clusters in all_external_clusters.items()},
        {layer: len(by_cluster) for layer, by_cluster in cluster_candidates.items()},
    )


def _population_quota(population: int) -> int:
    if population >= 10_000:
        return 3
    if population >= 100:
        return 2
    return 1 if population > 0 else 0


def _select_samples(
    taxonomy_path: Path,
    *,
    seed: str,
    sample_size: int,
    package_count: int,
    target_counts: Counter[str],
    target_heaps: Mapping[str, list[tuple[int, str]]],
    global_heap: list[tuple[int, str]],
    taxonomy_fingerprint: str,
) -> dict[str, object]:
    desired = min(max(sample_size, 0), package_count)
    origin_targets = sorted(
        (key for key in target_counts if key.startswith("originAreaCandidate=")),
        key=lambda key: (-int(target_counts[key]), key),
    )
    unknown_class_targets = sorted(
        (key for key in target_counts if key.startswith("unknownClassPath=")),
        key=lambda key: (-int(target_counts[key]), key),
    )
    top_origin_targets = set(origin_targets[:12])
    top_unknown_class_targets = set(unknown_class_targets[:6])
    active_target_counts = Counter(
        {
            key: int(population)
            for key, population in target_counts.items()
            if (not key.startswith("originAreaCandidate=") or key in top_origin_targets)
            and (
                not key.startswith("unknownClassPath=")
                or key in top_unknown_class_targets
            )
        }
    )
    active_targets = set(active_target_counts)
    candidate_names = {
        package_name
        for target, heap in target_heaps.items()
        if target in active_targets
        for _rank, package_name in heap
    }
    candidate_names.update(package_name for _rank, package_name in global_heap)
    external_cluster_limit = max(
        desired * QUOTA_CANDIDATE_MULTIPLIER,
        QUOTA_CANDIDATE_MINIMUM,
    )
    (
        candidates,
        external_cluster_counts,
        retained_external_cluster_counts,
    ) = _read_candidate_records(
        taxonomy_path,
        candidate_names,
        seed=seed,
        external_cluster_limit=external_cluster_limit,
    )
    candidate_targets = {
        name: set(_sample_targets(row)) & active_targets
        for name, row in candidates.items()
    }
    candidate_ranks = {
        name: _candidate_rank(seed, row) for name, row in candidates.items()
    }
    external_actor_max = (
        min(desired, max(1, math.ceil(desired * 0.20))) if desired else 0
    )
    external_object_max = (
        min(desired, max(1, math.ceil(desired * 0.05))) if desired else 0
    )
    layer_caps = {
        "WORLD_PARTITION_EXTERNAL_ACTOR": external_actor_max,
        "WORLD_PARTITION_EXTERNAL_OBJECT": external_object_max,
    }
    selected_layer_counts: Counter[str] = Counter()
    selected_external_clusters: set[tuple[str, str]] = set()

    def candidate_allowed(name: str) -> bool:
        row = candidates[name]
        layer = str(row["samplingLayer"])
        cap = layer_caps.get(layer)
        if cap is not None and selected_layer_counts[layer] >= cap:
            return False
        if (
            layer in layer_caps
            and (layer, str(row["sampleClusterKey"])) in selected_external_clusters
        ):
            return False
        return True

    def record_selected(name: str) -> None:
        row = candidates[name]
        layer = str(row["samplingLayer"])
        selected_layer_counts[layer] += 1
        if layer in layer_caps:
            selected_external_clusters.add((layer, str(row["sampleClusterKey"])))

    uncovered = set(active_target_counts)
    selected: list[tuple[str, list[str], str]] = []
    selected_names: set[str] = set()
    quota_achieved: Counter[str] = Counter()

    def add_selected(name: str, reasons: list[str]) -> None:
        selected.append((name, reasons, candidate_ranks[name]))
        selected_names.add(name)
        record_selected(name)
        for target in candidate_targets[name] & set(quota_targets):
            quota_achieved[target] += 1
        uncovered.difference_update(candidate_targets[name])

    def selected_count_for(target: str) -> int:
        return int(quota_achieved[target])

    quota_targets: dict[str, int] = {}
    for prefix in ("technicalFamily=", "confirmedSemanticRole="):
        for target in sorted(
            key for key in active_target_counts if key.startswith(prefix)
        ):
            quota_targets[target] = _population_quota(int(active_target_counts[target]))
    for prefix in ENUMERATED_CATEGORY_QUOTA_PREFIXES:
        for target in sorted(
            key for key in active_target_counts if key.startswith(prefix)
        ):
            quota_targets[target] = 1

    unknown_population = int(
        active_target_counts.get("classificationConfidence=UNKNOWN", 0)
    )
    if unknown_population and desired:
        unknown_minimum = max(3, math.ceil(desired * 0.10))
        if desired >= 32:
            unknown_minimum = max(16, unknown_minimum)
        unknown_minimum = min(unknown_population, desired, unknown_minimum)
        quota_targets["classificationConfidence=UNKNOWN"] = max(
            quota_targets.get("classificationConfidence=UNKNOWN", 0),
            unknown_minimum,
        )
    else:
        unknown_minimum = 0

    external_actor_population = int(
        active_target_counts.get("samplingLayer=WORLD_PARTITION_EXTERNAL_ACTOR", 0)
    )
    external_object_population = int(
        active_target_counts.get("samplingLayer=WORLD_PARTITION_EXTERNAL_OBJECT", 0)
    )
    external_actor_requested_min = (
        min(external_actor_population, external_actor_max, math.ceil(desired * 0.15))
        if external_actor_population and desired
        else 0
    )
    external_object_requested_min = (
        min(external_object_population, external_object_max, math.ceil(desired * 0.03))
        if external_object_population and desired
        else 0
    )
    external_actor_cluster_count = int(
        external_cluster_counts.get("WORLD_PARTITION_EXTERNAL_ACTOR", 0)
    )
    external_object_cluster_count = int(
        external_cluster_counts.get("WORLD_PARTITION_EXTERNAL_OBJECT", 0)
    )
    retained_external_actor_cluster_count = int(
        retained_external_cluster_counts.get("WORLD_PARTITION_EXTERNAL_ACTOR", 0)
    )
    retained_external_object_cluster_count = int(
        retained_external_cluster_counts.get("WORLD_PARTITION_EXTERNAL_OBJECT", 0)
    )
    external_actor_min = min(
        external_actor_requested_min, retained_external_actor_cluster_count
    )
    external_object_min = min(
        external_object_requested_min, retained_external_object_cluster_count
    )
    if external_actor_min:
        quota_targets["samplingLayer=WORLD_PARTITION_EXTERNAL_ACTOR"] = (
            external_actor_min
        )
    if external_object_min:
        quota_targets["samplingLayer=WORLD_PARTITION_EXTERNAL_OBJECT"] = (
            external_object_min
        )
    for target in sorted(top_unknown_class_targets):
        quota_targets[target] = 1
    for prefix in ("unknownClassProvenance=", "unknownPlacement="):
        for target in sorted(
            key for key in active_target_counts if key.startswith(prefix)
        ):
            quota_targets[target] = 1

    while len(selected) < desired:
        deficit_targets = {
            target
            for target, planned in quota_targets.items()
            if selected_count_for(target) < planned
        }
        if not deficit_targets:
            break
        best_quota: tuple[int, float, float, str, str, list[str]] | None = None
        for name, targets in candidate_targets.items():
            if name in selected_names or not candidate_allowed(name):
                continue
            quota_hits = sorted(targets & deficit_targets)
            if not quota_hits:
                continue
            quota_score = sum(
                _target_weight(target, int(active_target_counts[target]))
                for target in quota_hits
            )
            newly_covered = sorted(targets & uncovered)
            coverage_score = sum(
                _target_weight(target, int(active_target_counts[target]))
                for target in newly_covered
            )
            choice = (
                len(quota_hits),
                quota_score,
                coverage_score,
                candidate_ranks[name],
                name,
                quota_hits,
            )
            if (
                best_quota is None
                or choice[:3] > best_quota[:3]
                or (
                    choice[:3] == best_quota[:3]
                    and (choice[3], choice[4]) < (best_quota[3], best_quota[4])
                )
            ):
                best_quota = choice
        if best_quota is None:
            break
        _gain, _quota_score, _coverage_score, _rank, name, quota_hits = best_quota
        add_selected(
            name,
            [f"quotaMulticover:{target}" for target in quota_hits],
        )

    while len(selected) < desired and uncovered:
        best: tuple[float, str, str, list[str]] | None = None
        for name, targets in candidate_targets.items():
            if name in selected_names:
                continue
            if not candidate_allowed(name):
                continue
            newly_covered = sorted(targets & uncovered)
            if not newly_covered:
                continue
            score = sum(
                _target_weight(target, int(active_target_counts[target]))
                for target in newly_covered
            )
            rank = candidate_ranks[name]
            choice = (score, rank, name, newly_covered)
            if (
                best is None
                or score > best[0]
                or (score == best[0] and (rank, name) < (best[1], best[2]))
            ):
                best = choice
        if best is None:
            break
        _score, _rank, name, reasons = best
        add_selected(name, reasons)
    if len(selected) < desired:
        remaining = sorted(
            (
                (_candidate_rank(seed, row), name)
                for name, row in candidates.items()
                if name not in selected_names
            )
        )
        for rank, name in remaining:
            if len(selected) >= desired:
                break
            if not candidate_allowed(name):
                continue
            add_selected(name, ["deterministicFill"])

    def total_quota_shortfall() -> int:
        return sum(
            max(0, planned - selected_count_for(target))
            for target, planned in quota_targets.items()
        )

    def quota_counts_for(names: set[str] | frozenset[str]) -> Counter[str]:
        counts: Counter[str] = Counter()
        for name in names:
            for target in candidate_targets[name] & set(quota_targets):
                counts[target] += 1
        return counts

    def quota_shortfall_for(names: set[str] | frozenset[str]) -> int:
        counts = quota_counts_for(names)
        return sum(
            max(0, planned - int(counts[target]))
            for target, planned in quota_targets.items()
        )

    def selection_allowed(names: set[str] | frozenset[str]) -> bool:
        layer_counts: Counter[str] = Counter()
        clusters: set[tuple[str, str]] = set()
        for name in names:
            row = candidates[name]
            layer = str(row["samplingLayer"])
            layer_counts[layer] += 1
            cap = layer_caps.get(layer)
            if cap is not None and layer_counts[layer] > cap:
                return False
            if layer in layer_caps:
                cluster = (layer, str(row["sampleClusterKey"]))
                if cluster in clusters:
                    return False
                clusters.add(cluster)
        return True

    def rebuild_selected_state() -> None:
        selected_layer_counts.clear()
        selected_external_clusters.clear()
        quota_achieved.clear()
        for selected_name in selected_names:
            record_selected(selected_name)
            for target in candidate_targets[selected_name] & set(quota_targets):
                quota_achieved[target] += 1

    initial_quota_shortfall = total_quota_shortfall()
    one_swap_repairs_applied = 0
    depth_two_repairs_applied = 0

    def swap_allowed(remove_name: str, add_name: str) -> bool:
        remove_row = candidates[remove_name]
        add_row = candidates[add_name]
        remove_layer = str(remove_row["samplingLayer"])
        add_layer = str(add_row["samplingLayer"])
        add_count = (
            selected_layer_counts[add_layer]
            - (1 if add_layer == remove_layer else 0)
            + 1
        )
        cap = layer_caps.get(add_layer)
        if cap is not None and add_count > cap:
            return False
        if add_layer in layer_caps:
            add_cluster = (add_layer, str(add_row["sampleClusterKey"]))
            for selected_name in selected_names:
                if selected_name == remove_name:
                    continue
                selected_row = candidates[selected_name]
                selected_cluster = (
                    str(selected_row["samplingLayer"]),
                    str(selected_row["sampleClusterKey"]),
                )
                if selected_cluster == add_cluster:
                    return False
        return True

    while True:
        current_shortfall = total_quota_shortfall()
        if current_shortfall == 0:
            break
        deficit_targets = {
            target
            for target, planned in quota_targets.items()
            if selected_count_for(target) < planned
        }
        best_swap: tuple[int, str, str, str, str] | None = None
        for add_name, add_targets in candidate_targets.items():
            if add_name in selected_names or not (add_targets & deficit_targets):
                continue
            for remove_name in selected_names:
                if not swap_allowed(remove_name, add_name):
                    continue
                affected = (candidate_targets[remove_name] | add_targets) & set(
                    quota_targets
                )
                candidate_shortfall = current_shortfall
                for target in affected:
                    before_count = selected_count_for(target)
                    after_count = (
                        before_count
                        - (1 if target in candidate_targets[remove_name] else 0)
                        + (1 if target in add_targets else 0)
                    )
                    planned = quota_targets[target]
                    candidate_shortfall += max(0, planned - after_count) - max(
                        0, planned - before_count
                    )
                if candidate_shortfall >= current_shortfall:
                    continue
                choice = (
                    candidate_shortfall,
                    candidate_ranks[add_name],
                    candidate_ranks[remove_name],
                    add_name,
                    remove_name,
                )
                if best_swap is None or choice < best_swap:
                    best_swap = choice
        if best_swap is None:
            break
        _new_shortfall, _add_rank, _remove_rank, add_name, remove_name = best_swap
        replace_index = next(
            index for index, item in enumerate(selected) if item[0] == remove_name
        )
        repaired_targets = sorted(candidate_targets[add_name] & deficit_targets)
        selected[replace_index] = (
            add_name,
            [f"quotaRepair:{target}" for target in repaired_targets],
            candidate_ranks[add_name],
        )
        selected_names.remove(remove_name)
        selected_names.add(add_name)
        rebuild_selected_state()
        one_swap_repairs_applied += 1

    def bounded_add_pool(
        names: set[str] | frozenset[str], deficit_targets: set[str]
    ) -> list[str]:
        pool: set[str] = set()
        for target in sorted(deficit_targets):
            ranked = sorted(
                (
                    candidate_ranks[name],
                    name,
                )
                for name, targets in candidate_targets.items()
                if name not in names and target in targets
            )
            pool.update(
                name
                for _rank, name in ranked[:QUOTA_REPAIR_MAX_ADD_CANDIDATES_PER_DEFICIT]
            )
        return sorted(pool, key=lambda name: (candidate_ranks[name], name))

    while total_quota_shortfall() > 0:
        base_names = frozenset(selected_names)
        base_shortfall = quota_shortfall_for(base_names)
        base_counts = quota_counts_for(base_names)
        base_deficits = {
            target
            for target, planned in quota_targets.items()
            if int(base_counts[target]) < planned
        }
        selected_order = sorted(
            base_names, key=lambda name: (candidate_ranks[name], name)
        )
        neutral_states: list[frozenset[str]] = []
        improved_state: frozenset[str] | None = None
        for add_name in bounded_add_pool(base_names, base_deficits):
            for remove_name in selected_order:
                trial = frozenset((base_names - {remove_name}) | {add_name})
                if not selection_allowed(trial):
                    continue
                trial_shortfall = quota_shortfall_for(trial)
                if trial_shortfall < base_shortfall:
                    improved_state = trial
                    break
                if (
                    trial_shortfall == base_shortfall
                    and trial not in neutral_states
                    and len(neutral_states) < QUOTA_REPAIR_MAX_NEUTRAL_STATES
                ):
                    neutral_states.append(trial)
            if improved_state is not None:
                break
        if improved_state is None:
            for neutral in neutral_states:
                neutral_counts = quota_counts_for(neutral)
                neutral_deficits = {
                    target
                    for target, planned in quota_targets.items()
                    if int(neutral_counts[target]) < planned
                }
                neutral_order = sorted(
                    neutral, key=lambda name: (candidate_ranks[name], name)
                )
                for add_name in bounded_add_pool(neutral, neutral_deficits):
                    for remove_name in neutral_order:
                        trial = frozenset((neutral - {remove_name}) | {add_name})
                        if not selection_allowed(trial):
                            continue
                        if quota_shortfall_for(trial) < base_shortfall:
                            improved_state = trial
                            break
                    if improved_state is not None:
                        break
                if improved_state is not None:
                    break
        if improved_state is None:
            break
        removed_names = [name for name in selected_names if name not in improved_state]
        added_names = sorted(
            (name for name in improved_state if name not in selected_names),
            key=lambda name: (candidate_ranks[name], name),
        )
        replace_indices = sorted(
            next(index for index, item in enumerate(selected) if item[0] == name)
            for name in removed_names
        )
        repaired_targets = sorted(base_deficits)
        for index, add_name in zip(replace_indices, added_names, strict=True):
            selected[index] = (
                add_name,
                [f"quotaRepairDepth2:{target}" for target in repaired_targets],
                candidate_ranks[add_name],
            )
        selected_names = set(improved_state)
        rebuild_selected_state()
        depth_two_repairs_applied += 1

    final_quota_shortfall = total_quota_shortfall()
    quota_repair_status = (
        "NOT_NEEDED"
        if initial_quota_shortfall == 0
        else "ACHIEVED"
        if final_quota_shortfall == 0
        else "BOUNDED_SEARCH_EXHAUSTED_WITH_SHORTFALL"
    )

    achieved_quota_targets = {
        target: selected_count_for(target) for target in sorted(quota_targets)
    }
    unmet_quota_targets = [
        {
            "target": target,
            "planned": quota_targets[target],
            "actual": achieved_quota_targets[target],
            "shortfall": quota_targets[target] - achieved_quota_targets[target],
        }
        for target in sorted(quota_targets)
        if achieved_quota_targets[target] < quota_targets[target]
    ]
    samples = []
    covered = set()
    for index, (name, reasons, rank) in enumerate(selected, 1):
        row = candidates[name]
        covered.update(set(_sample_targets(row)) & active_targets)
        unknown_paths = _unknown_class_paths(row)
        samples.append(
            {
                "selectionOrder": index,
                "packageName": name,
                "profileFingerprint": row["profileFingerprint"],
                "candidateHash": rank,
                "selectionReasons": reasons,
                "dimensions": {
                    "technicalFamily": row["primaryTechnicalFamily"],
                    "technicalKind": row["primaryTechnicalKind"],
                    "sourceScope": row["sourceScope"],
                    "mountPoint": row["mountPoint"],
                    "storageArea": row["storageArea"],
                    "originArea": row["originArea"],
                    "originAreaStatus": row["originAreaStatus"],
                    "ownerWorldPathCandidate": row["ownerWorldPath"],
                    "ownerWorldPathStatus": row["ownerWorldPathStatus"],
                    "layoutKind": row["layoutKind"],
                    "packageShape": row["packageShape"],
                    "classificationConfidence": row["classificationConfidence"],
                    "semanticStatus": row["semanticStatus"],
                    "samplingLayer": row["samplingLayer"],
                    "sampleClusterKey": row["sampleClusterKey"],
                    "unknownAssetClassPaths": unknown_paths,
                    "confirmedSemanticRoles": [
                        anchor["role"] for anchor in row["confirmedSemanticAnchors"]
                    ],
                    "candidateSemanticRoles": [
                        candidate["role"] for candidate in row["semanticCandidates"]
                    ],
                    "availableCapabilities": row["availableCapabilities"],
                    "acquisitionRoutes": row["acquisitionRoutes"],
                    "capabilityStatus": row["capabilityStatus"],
                },
            }
        )
    if len(samples) != desired or unmet_quota_targets:
        sample_status = "DEGRADED"
    elif covered != active_targets:
        sample_status = "COVERAGE_PARTIAL"
    else:
        sample_status = "COMPLETE"
    return {
        "schema": SAMPLE_MANIFEST_SCHEMA,
        "status": sample_status,
        "selectionAlgorithm": SAMPLE_ALGORITHM_VERSION,
        "samplerPolicy": _sampler_policy_summary(sample_size),
        "seed": seed,
        "taxonomyFingerprint": taxonomy_fingerprint,
        "requestedSampleSize": sample_size,
        "actualSampleSize": len(samples),
        "populationPackageCount": package_count,
        "samplingConstraints": {
            "worldPartitionExternalActorMax": external_actor_max,
            "worldPartitionExternalObjectMax": external_object_max,
            "worldPartitionExternalActorRequestedMin": external_actor_requested_min,
            "worldPartitionExternalObjectRequestedMin": external_object_requested_min,
            "worldPartitionExternalActorDistinctClusterCount": external_actor_cluster_count,
            "worldPartitionExternalObjectDistinctClusterCount": external_object_cluster_count,
            "worldPartitionExternalActorRetainedDistinctClusterCount": retained_external_actor_cluster_count,
            "worldPartitionExternalObjectRetainedDistinctClusterCount": retained_external_object_cluster_count,
            "worldPartitionExternalActorMin": external_actor_min,
            "worldPartitionExternalObjectMin": external_object_min,
            "worldPartitionClusterKey": "samplingLayer+ownerWorldPathCandidate+actorClass",
            "technicalFamilyQuotaFormula": {
                "population1To99": 1,
                "population100To9999": 2,
                "population10000OrMore": 3,
            },
            "confirmedSemanticRoleQuotaFormula": {
                "population1To99": 1,
                "population100To9999": 2,
                "population10000OrMore": 3,
            },
            "enumeratedCategoryMinimums": {
                prefix.removesuffix("="): 1
                for prefix in ENUMERATED_CATEGORY_QUOTA_PREFIXES
            },
            "technicallyUnknownMinimum": unknown_minimum,
            "quotaRepairSearch": {
                "strategy": "strict-one-swap-then-bounded-depth-two-neutral-search",
                "exhaustive": False,
                "status": quota_repair_status,
                "initialShortfall": initial_quota_shortfall,
                "finalShortfall": final_quota_shortfall,
                "oneSwapRepairsApplied": one_swap_repairs_applied,
                "depthTwoRepairsApplied": depth_two_repairs_applied,
                "maxDepth": 2,
                "maxAddCandidatesPerDeficit": QUOTA_REPAIR_MAX_ADD_CANDIDATES_PER_DEFICIT,
                "maxNeutralStates": QUOTA_REPAIR_MAX_NEUTRAL_STATES,
            },
            "plannedQuotaTargets": {
                key: quota_targets[key] for key in sorted(quota_targets)
            },
            "achievedQuotaTargets": achieved_quota_targets,
            "unmetQuotaTargets": unmet_quota_targets,
        },
        "coverage": {
            "policy": {
                "topOriginAreas": len(top_origin_targets),
                "originAreaTailHashBuckets": 16,
                "topUnknownClassPaths": len(top_unknown_class_targets),
                "unknownClassHashBuckets": 16,
                "detailedOriginAreasNotIndividuallyOptimized": max(
                    0, len(origin_targets) - len(top_origin_targets)
                ),
                "detailedUnknownClassPathsNotIndividuallyOptimized": max(
                    0,
                    len(unknown_class_targets) - len(top_unknown_class_targets),
                ),
            },
            "totalTargets": len(active_target_counts),
            "coveredTargets": len(covered),
            "uncoveredTargets": sorted(active_targets - covered),
            "targetPopulations": {
                key: int(active_target_counts[key])
                for key in sorted(active_target_counts)
            },
        },
        "samples": samples,
    }
