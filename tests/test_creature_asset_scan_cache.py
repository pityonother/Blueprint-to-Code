import os
import tempfile
import unittest
from pathlib import Path


import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.creature_asset_scan_cache import (  # noqa: E402
    CreatureAssetScanCache,
)


class CreatureAssetScanCacheTests(unittest.TestCase):
    def test_reuses_unchanged_projected_asset_fact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset = root / "Dino_Character_BP.uasset"
            asset.write_bytes(b"first")
            cache_path = root / "cache.json"
            calls = 0

            def extract(_path):
                nonlocal calls
                calls += 1
                return {"parent": "PrimalDinoCharacter", "properties": []}

            cache = CreatureAssetScanCache(cache_path)
            first, first_hit = cache.get_or_extract(asset, extract)
            cache.flush()
            reopened = CreatureAssetScanCache(cache_path)
            second, second_hit = reopened.get_or_extract(asset, extract)

        self.assertFalse(first_hit)
        self.assertTrue(second_hit)
        self.assertEqual(first, second)
        self.assertEqual(calls, 1)

    def test_invalidates_same_size_same_mtime_content_replacement(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset = root / "Dino_Character_BP.uasset"
            asset.write_bytes(b"first")
            original_stat = asset.stat()
            cache = CreatureAssetScanCache(root / "cache.json")
            cache.get_or_extract(asset, lambda _path: {"value": "old"})
            cache.flush()

            asset.write_bytes(b"other")
            os.utime(asset, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
            reopened = CreatureAssetScanCache(root / "cache.json")
            fact, hit = reopened.get_or_extract(asset, lambda _path: {"value": "new"})

        self.assertFalse(hit)
        self.assertEqual(fact["value"], "new")
        self.assertEqual(reopened.invalidated, 1)

    def test_extractor_version_mismatch_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset = root / "Dino_Character_BP.uasset"
            asset.write_bytes(b"asset")
            cache_path = root / "cache.json"
            first = CreatureAssetScanCache(cache_path, extractor_version="v1")
            first.get_or_extract(asset, lambda _path: {"value": "old"})
            first.flush()

            changed = CreatureAssetScanCache(cache_path, extractor_version="v2")
            fact, hit = changed.get_or_extract(asset, lambda _path: {"value": "new"})

        self.assertEqual(changed.load_status, "VERSION_MISMATCH_IGNORED")
        self.assertFalse(hit)
        self.assertEqual(fact["value"], "new")


if __name__ == "__main__":
    unittest.main()
