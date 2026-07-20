import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from rank_ark_harvest import resolve_creatures  # noqa: E402


class _UnparsedAttackReader:
    @staticmethod
    def defaults(_path: Path) -> dict[str, object]:
        return {
            "properties": [
                {
                    "name": "AttackInfos",
                    "type": "ArrayProperty",
                    "array_parse": {
                        "parsed": False,
                        "error": "synthetic decode failure",
                    },
                }
            ],
            "warnings": [],
        }


class CreatureAttackFailureTests(unittest.TestCase):
    def test_unparsed_attack_infos_is_reported_as_a_semantic_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset_path = root / "BrokenCreature.uasset"
            asset_path.write_bytes(b"fixture")
            with patch(
                "rank_ark_harvest.object_path_to_uasset_path",
                return_value=(asset_path, [asset_path]),
            ):
                creatures, failures = resolve_creatures(
                    [
                        {
                            "name": "BrokenCreature",
                            "objectPath": "/Game/Test/BrokenCreature.BrokenCreature",
                        }
                    ],
                    root,
                    _UnparsedAttackReader(),
                )

        self.assertEqual(len(creatures), 1)
        self.assertEqual(creatures[0]["attacks"], [])
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["reasonCode"], "ATTACK_INFOS_NOT_RECOVERED")
        self.assertEqual(failures[0]["name"], "BrokenCreature")
        self.assertEqual(failures[0]["objectPath"], "/Game/Test/BrokenCreature.BrokenCreature")


if __name__ == "__main__":
    unittest.main()
