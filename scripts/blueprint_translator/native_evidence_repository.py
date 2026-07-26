"""Fail-closed repository for one Native Evidence artifact directory."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .native_evidence_query import NativeEvidenceQueryService
from .native_evidence_store import (
    NATIVE_MANIFEST_SCHEMA,
    NATIVE_SQLITE_SCHEMA,
    NATIVE_SQLITE_USER_VERSION,
    NATIVE_TABLES,
    sha256_file,
)


class NativeEvidenceArtifactInvalid(ValueError):
    """The JSON/manifest/SQLite artifact set is inconsistent or corrupt."""


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NativeEvidenceArtifactInvalid(f"{label} cannot be read: {exc}") from exc
    if not isinstance(payload, dict):
        raise NativeEvidenceArtifactInvalid(f"{label} must contain a JSON object")
    return payload


def _inside(root: Path, relative: object, label: str) -> Path:
    value = str(relative or "").strip()
    if not value:
        raise NativeEvidenceArtifactInvalid(f"{label} path is missing")
    candidate = (root / value).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise NativeEvidenceArtifactInvalid(
            f"{label} path escapes the evidence directory"
        ) from exc
    return candidate


class NativeEvidenceRepository:
    """Read-only repository whose source identity is checked before every open."""

    def __init__(
        self,
        *,
        root: Path,
        source_path: Path,
        database_path: Path,
        manifest_path: Path,
        manifest: dict[str, Any],
        connection: sqlite3.Connection,
        evidence_set_id: str,
        source_sha256: str,
    ) -> None:
        self.root = root
        self.source_path = source_path
        self.database_path = database_path
        self.manifest_path = manifest_path
        self.manifest = manifest
        self._connection = connection
        self._closed = False
        self.evidence_set_id = evidence_set_id
        self.source_sha256 = source_sha256
        self._query = NativeEvidenceQueryService(
            connection,
            evidence_set_id=evidence_set_id,
            source_sha256=source_sha256,
        )

    @classmethod
    def open(cls, evidence_dir: str | Path) -> "NativeEvidenceRepository":
        root = Path(evidence_dir).expanduser().resolve()
        manifest_path = root / "evidence.manifest.json"
        manifest = _read_object(manifest_path, "native evidence manifest")
        if manifest.get("schema") != NATIVE_MANIFEST_SCHEMA:
            raise NativeEvidenceArtifactInvalid(
                "native evidence manifest schema is invalid"
            )
        trust_meta = manifest.get("trust")
        if not isinstance(trust_meta, dict):
            raise NativeEvidenceArtifactInvalid(
                "native evidence manifest trust metadata is missing"
            )
        trust_status = str(trust_meta.get("status") or "").strip().upper()
        formal_validation = trust_meta.get("formalValidation")
        if not trust_status or not isinstance(formal_validation, bool):
            raise NativeEvidenceArtifactInvalid(
                "native evidence manifest trust metadata is invalid"
            )
        if formal_validation and trust_status != "VERIFIED":
            raise NativeEvidenceArtifactInvalid(
                "formal Native Evidence manifest is not VERIFIED"
            )
        source_meta = manifest.get("source")
        sqlite_meta = manifest.get("sqlite")
        if not isinstance(source_meta, dict) or not isinstance(sqlite_meta, dict):
            raise NativeEvidenceArtifactInvalid(
                "native evidence manifest source/SQLite metadata is incomplete"
            )
        source_path = _inside(root, source_meta.get("path"), "source JSON")
        database_path = _inside(root, sqlite_meta.get("path"), "SQLite companion")
        expected_source_hash = str(source_meta.get("sha256") or "").casefold()
        expected_database_hash = str(sqlite_meta.get("sha256") or "").casefold()
        try:
            actual_source_hash = sha256_file(source_path)
        except OSError as exc:
            raise NativeEvidenceArtifactInvalid(
                f"source JSON cannot be verified: {exc}"
            ) from exc
        if actual_source_hash != expected_source_hash:
            raise NativeEvidenceArtifactInvalid(
                "source JSON hash mismatch; rebuild the SQLite companion"
            )
        try:
            actual_database_hash = sha256_file(database_path)
        except OSError as exc:
            raise NativeEvidenceArtifactInvalid(
                f"SQLite companion cannot be verified: {exc}"
            ) from exc
        if actual_database_hash != expected_database_hash:
            raise NativeEvidenceArtifactInvalid(
                "SQLite companion hash mismatch; rebuild from authoritative JSON"
            )
        if sqlite_meta.get("schema") != NATIVE_SQLITE_SCHEMA:
            raise NativeEvidenceArtifactInvalid("SQLite companion schema is invalid")

        uri = f"{database_path.as_uri()}?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA query_only = ON")
            user_version = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
            if user_version != NATIVE_SQLITE_USER_VERSION:
                raise NativeEvidenceArtifactInvalid(
                    "SQLite companion user_version is invalid"
                )
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise NativeEvidenceArtifactInvalid(
                    f"SQLite companion integrity check failed: {integrity}"
                )
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_keys:
                raise NativeEvidenceArtifactInvalid(
                    f"SQLite companion foreign key check failed: {foreign_keys[:3]}"
                )
            identity = connection.execute(
                "SELECT evidence_set_id, sqlite_schema, source_json_sha256, "
                "source_json_size, provenance_status "
                "FROM native_evidence_sets LIMIT 1"
            ).fetchone()
            if identity is None:
                raise NativeEvidenceArtifactInvalid(
                    "SQLite companion has no evidence set"
                )
            evidence_set_id = str(identity["evidence_set_id"])
            if evidence_set_id != str(manifest.get("evidenceSetId") or ""):
                raise NativeEvidenceArtifactInvalid(
                    "manifest evidence set differs from SQLite"
                )
            if str(identity["sqlite_schema"]) != NATIVE_SQLITE_SCHEMA:
                raise NativeEvidenceArtifactInvalid(
                    "SQLite evidence-set schema marker is invalid"
                )
            if str(identity["source_json_sha256"]) != actual_source_hash:
                raise NativeEvidenceArtifactInvalid(
                    "SQLite source hash differs from authoritative JSON"
                )
            if str(identity["provenance_status"]) != trust_status:
                raise NativeEvidenceArtifactInvalid(
                    "manifest trust status differs from SQLite"
                )
            if int(identity["source_json_size"]) != source_path.stat().st_size:
                raise NativeEvidenceArtifactInvalid(
                    "SQLite source size differs from authoritative JSON"
                )
            counts = manifest.get("counts")
            if not isinstance(counts, dict):
                raise NativeEvidenceArtifactInvalid(
                    "native evidence manifest counts are missing"
                )
            for table in (*NATIVE_TABLES, "native_blueprint_links"):
                actual = int(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                )
                if int(counts.get(table, -1)) != actual:
                    raise NativeEvidenceArtifactInvalid(
                        f"manifest count differs from SQLite for {table}"
                    )
        except Exception:
            if "connection" in locals():
                connection.close()
            raise
        return cls(
            root=root,
            source_path=source_path,
            database_path=database_path,
            manifest_path=manifest_path,
            manifest=manifest,
            connection=connection,
            evidence_set_id=evidence_set_id,
            source_sha256=actual_source_hash,
        )

    def query(self, request: dict[str, object]) -> dict[str, object]:
        if self._closed:
            raise RuntimeError("NativeEvidenceRepository is closed")
        return self._query.query(request)

    def list_functions(self) -> list[dict[str, object]]:
        if self._closed:
            raise RuntimeError("NativeEvidenceRepository is closed")
        rows = self._connection.execute(
            "SELECT evidence_id, name, qualified_name, owner, rva, signature, "
            "status, confidence, source FROM native_functions "
            "ORDER BY qualified_name, evidence_id"
        ).fetchall()
        return [
            {
                "evidenceId": str(row["evidence_id"]),
                "name": str(row["name"]),
                "qualifiedName": str(row["qualified_name"]),
                "owner": str(row["owner"]),
                "rva": str(row["rva"]),
                "signature": str(row["signature"]),
                "status": str(row["status"]),
                "confidence": str(row["confidence"]),
                "source": str(row["source"]),
            }
            for row in rows
        ]

    def close(self) -> None:
        if not self._closed:
            self._connection.close()
            self._closed = True

    def __enter__(self) -> "NativeEvidenceRepository":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()


def open_native_evidence_repository(
    evidence_dir: str | Path,
) -> NativeEvidenceRepository:
    return NativeEvidenceRepository.open(evidence_dir)
