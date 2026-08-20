from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_btc_delivery import (  # noqa: E402
    COMPATIBILITY_ALIASES,
    PRIMARY_ARTIFACTS,
    build_delivery,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _result() -> dict[str, object]:
    return {
        "schema": "ark.btc.category-capability-benchmark.v1",
        "status": "PASS",
        "counts": {
            "samples": 2,
            "evidenceAvailability": {"FORMAL_QUERY": 2},
            "answerClosure": {"COMPLETE": 1, "PARTIAL": 1},
        },
        "inputBindings": {"samplePlan": {"sha256": "a" * 64}},
        "samples": [
            {
                "sampleIndex": 1,
                "plannedStratum": {"labelZh": "普通蓝图"},
                "targetPath": "/Game/Test/BP_One.BP_One",
                "route": {"effectiveReader": "BLUEPRINT_EVIDENCE"},
                "axes": {
                    "evidenceAvailability": "FORMAL_QUERY",
                    "answerClosure": "COMPLETE",
                },
                "statusZh": "可正式查询",
                "result": {
                    "question": {"questionZh": "默认数值是多少？"},
                    "claims": [
                        {
                            "textZh": "默认数值为 6。",
                            "evidenceRefs": ["bp://asset@revision/default/Value"],
                        }
                    ],
                    "blockingGaps": [],
                },
            },
            {
                "sampleIndex": 2,
                "plannedStratum": {"labelZh": "原生类"},
                "targetPath": "/Script/Test.NativeThing",
                "route": {"effectiveReader": "NATIVE_EVIDENCE"},
                "axes": {
                    "evidenceAvailability": "FORMAL_QUERY",
                    "answerClosure": "PARTIAL",
                },
                "statusZh": "只能回答一部分",
                "result": {
                    "question": {"questionZh": "它怎样工作？"},
                    "claims": [],
                    "blockingGaps": [
                        "READY 但不是 FRESH，releaseAuthority 也不能替代问题闭环。"
                    ],
                },
            },
        ],
    }


class BuildBtcDeliveryTests(unittest.TestCase):
    def test_builds_only_five_primary_files_bound_to_one_result_hash(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            result_source = root / "work" / "policy-live-after.json"
            query_source = root / "work" / "reproduce.ps1"
            output = root / "delivery"
            result_source.parent.mkdir()
            result_source.write_text(
                json.dumps(_result(), ensure_ascii=False),
                encoding="utf-8",
            )
            query_source.write_text(
                "param([string]$ProjectRoot)\nWrite-Output $ProjectRoot\n",
                encoding="utf-8",
            )

            manifest = build_delivery(
                result_source=result_source,
                query_source=query_source,
                output_dir=output,
            )

            self.assertEqual(
                {path.name for path in output.iterdir()},
                set(PRIMARY_ARTIFACTS),
            )
            schema = json.loads(
                (ROOT / "schemas" / "btc_delivery_manifest_v1.schema.json").read_text(
                    encoding="utf-8"
                )
            )
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema).validate(manifest)
            result_sha256 = _sha256(output / "result.json")
            self.assertEqual(manifest["resultSha256"], result_sha256)
            for name in (
                "artifact-manifest.json",
                "report.md",
                "query-examples.ps1",
                "SHA256SUMS.txt",
            ):
                self.assertIn(result_sha256, (output / name).read_text("utf-8"))

            artifacts = {
                item["name"]: item
                for item in manifest["artifacts"]
            }
            for name in (
                "result.json",
                "report.md",
                "query-examples.ps1",
            ):
                self.assertEqual(artifacts[name]["sha256"], _sha256(output / name))
            checksum_entries = {}
            for line in (output / "SHA256SUMS.txt").read_text("utf-8").splitlines():
                if not line or line.startswith("#"):
                    continue
                digest, name = line.split("  ", 1)
                checksum_entries[name] = digest
            self.assertEqual(
                set(checksum_entries),
                {
                    "artifact-manifest.json",
                    "result.json",
                    "report.md",
                    "query-examples.ps1",
                },
            )
            for name, digest in checksum_entries.items():
                self.assertEqual(digest, _sha256(output / name))
            self.assertEqual(
                set(manifest["compatibilityAliases"]),
                set(COMPATIBILITY_ALIASES),
            )
            self.assertTrue(manifest["compatibilityPolicy"]["removalNotAuthorized"])
            first_hashes = {
                name: _sha256(output / name) for name in PRIMARY_ARTIFACTS
            }
            build_delivery(
                result_source=result_source,
                query_source=query_source,
                output_dir=output,
            )
            self.assertEqual(
                {_name: _sha256(output / _name) for _name in PRIMARY_ARTIFACTS},
                first_hashes,
            )

    def test_report_uses_chinese_projection_not_internal_gate_words(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            result_source = root / "result-source.json"
            query_source = root / "query-source.ps1"
            result_source.write_text(
                json.dumps(_result(), ensure_ascii=False),
                encoding="utf-8",
            )
            query_source.write_text("Write-Output 'query'\n", encoding="utf-8")

            build_delivery(
                result_source=result_source,
                query_source=query_source,
                output_dir=root / "delivery",
            )

            report = (root / "delivery" / "report.md").read_text("utf-8")
            self.assertIn("可正式查询", report)
            self.assertIn("只能回答一部分", report)
            self.assertIn("与当前资产一致", report)
            self.assertIn("可作为正式证据", report)
            self.assertIn("bp://asset@revision/default/Value", report)
            for forbidden in ("READY", "FRESH", "releaseAuthority"):
                self.assertNotIn(forbidden, report)

    def test_existing_unknown_file_fails_without_deleting_it(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            result_source = root / "result-source.json"
            query_source = root / "query-source.ps1"
            output = root / "delivery"
            output.mkdir()
            sentinel = output / "user-notes.txt"
            sentinel.write_text("preserve me", encoding="utf-8")
            result_source.write_text(
                json.dumps(_result(), ensure_ascii=False),
                encoding="utf-8",
            )
            query_source.write_text("Write-Output 'query'\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "unexpected files"):
                build_delivery(
                    result_source=result_source,
                    query_source=query_source,
                    output_dir=output,
                )

            self.assertEqual(sentinel.read_text("utf-8"), "preserve me")

    def test_machine_local_path_in_result_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            payload = _result()
            payload["debugPath"] = r"C:\\Users\\someone\\secret.db"
            result_source = root / "result-source.json"
            query_source = root / "query-source.ps1"
            result_source.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            query_source.write_text("Write-Output 'query'\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "machine-local path"):
                build_delivery(
                    result_source=result_source,
                    query_source=query_source,
                    output_dir=root / "delivery",
                )


if __name__ == "__main__":
    unittest.main()
