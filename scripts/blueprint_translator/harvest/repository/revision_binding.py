"""Evaluation manifest and revision binding for repository queries."""

from __future__ import annotations

import re
from typing import Any

from ..contracts import YIELD_MODEL_VERSION
from ..evaluation import (
    EVALUATION_CATALOG_SCHEMA,
    HARVEST_RANKING_CONTRACT_VERSION,
    HARVEST_RANKING_POLICY_VERSION,
    HarvestEvaluationEngine,
)
from .dataset_loader import HarvestDatasetInvalid, HarvestDatasetNotBuilt


_REVISION_PATTERN = re.compile(r"[0-9a-f]{64}")


class RevisionBindingMixin:
    @staticmethod
    def _validated_revision(value: object, label: str) -> str:
        revision = str(value or "")
        if _REVISION_PATTERN.fullmatch(revision) is None:
            raise HarvestDatasetInvalid(
                f"{label} must be a 64-character lowercase SHA-256 revision."
            )
        return revision

    def _load_evaluation(
        self,
    ) -> tuple[dict[str, Any], HarvestEvaluationEngine]:
        path = self.evaluation_catalog_path
        if path is None:
            raise HarvestDatasetInvalid("Harvest evaluation catalog is not configured.")
        try:
            signature = self._signature(path)
        except FileNotFoundError as exc:
            raise HarvestDatasetNotBuilt(
                "Harvest evaluation catalog has not been generated."
            ) from exc
        with self._lock:
            if self._evaluation is None or signature != self._evaluation_signature:
                payload = self._read_object(path, "Harvest evaluation catalog")
                if payload.get("schema") != EVALUATION_CATALOG_SCHEMA:
                    raise HarvestDatasetInvalid(
                        "Harvest evaluation catalog schema is invalid."
                    )
                dataset = payload.get("dataset")
                if not isinstance(dataset, dict):
                    raise HarvestDatasetInvalid(
                        "Harvest evaluation catalog dataset metadata is invalid."
                    )
                self._validated_revision(
                    dataset.get("revision"),
                    "Harvest evaluation catalog revision",
                )
                self._validated_revision(
                    dataset.get("componentDatasetRevision"),
                    "Harvest evaluation component revision",
                )
                methodology = payload.get("methodology")
                if (
                    isinstance(methodology, dict)
                    and methodology.get("contractVersion")
                    == HARVEST_RANKING_CONTRACT_VERSION
                ):
                    expected_identity = {
                        "formulaVersion": YIELD_MODEL_VERSION,
                        "policyVersion": HARVEST_RANKING_POLICY_VERSION,
                    }
                    for key, expected in expected_identity.items():
                        if methodology.get(key) != expected:
                            raise HarvestDatasetInvalid(
                                f"Harvest evaluation {key} does not match this runtime."
                            )
                    if not str(dataset.get("extractorVersion") or ""):
                        raise HarvestDatasetInvalid(
                            "Harvest evaluation extractor version is missing."
                        )
                try:
                    engine = HarvestEvaluationEngine(payload)
                except (TypeError, ValueError) as exc:
                    raise HarvestDatasetInvalid(str(exc)) from exc
                self._evaluation = payload
                self._evaluation_engine = engine
                self._evaluation_signature = signature
                self._lazy_ranking_cache.clear()
                self._top_baseline_cache.clear()
                self._creature_pair_cache.clear()
                self._v2_tier_baseline_cache.clear()
                self._specialty_response_cache.clear()
            if self._evaluation_engine is None:
                raise HarvestDatasetInvalid(
                    "Harvest evaluation catalog engine is unavailable."
                )
            return self._evaluation, self._evaluation_engine

    @staticmethod
    def _runtime_identity(
        node_catalog: dict[str, Any],
        evaluation_catalog: dict[str, Any],
    ) -> dict[str, str]:
        node_dataset = dict(node_catalog.get("dataset") or {})
        evaluation_dataset = dict(evaluation_catalog.get("dataset") or {})
        return {
            "extractorVersion": str(
                evaluation_dataset.get("extractorVersion") or ""
            ),
            "policyVersion": HARVEST_RANKING_POLICY_VERSION,
            "nodeCatalogRevision": str(node_dataset.get("revision") or ""),
            "evaluationCatalogRevision": str(
                evaluation_dataset.get("revision") or ""
            ),
            "componentCatalogRevision": str(
                evaluation_dataset.get("componentDatasetRevision") or ""
            ),
        }

    @staticmethod
    def _evaluation_revisions(
        node_catalog: dict[str, Any],
        evaluation_catalog: dict[str, Any],
    ) -> tuple[str, str]:
        node_dataset = node_catalog.get("dataset")
        evaluation_dataset = evaluation_catalog.get("dataset")
        if not isinstance(node_dataset, dict) or not isinstance(
            evaluation_dataset, dict
        ):
            raise HarvestDatasetInvalid(
                "Harvest node/evaluation dataset metadata is invalid."
            )
        expected_evaluation = RevisionBindingMixin._validated_revision(
            node_dataset.get("evaluationDatasetRevision"),
            "Resource-node evaluation revision",
        )
        expected_component = RevisionBindingMixin._validated_revision(
            node_dataset.get("componentDatasetRevision"),
            "Resource-node component revision",
        )
        actual_evaluation = RevisionBindingMixin._validated_revision(
            evaluation_dataset.get("revision"),
            "Harvest evaluation catalog revision",
        )
        actual_component = RevisionBindingMixin._validated_revision(
            evaluation_dataset.get("componentDatasetRevision"),
            "Harvest evaluation component revision",
        )
        if expected_evaluation != actual_evaluation:
            raise HarvestDatasetInvalid(
                "Resource-node catalog and evaluation catalog revisions do not match."
            )
        if expected_component != actual_component:
            raise HarvestDatasetInvalid(
                "Resource-node catalog and evaluation component revisions do not match."
            )
        return actual_evaluation, actual_component
