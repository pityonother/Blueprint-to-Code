import os
import tempfile
import unittest
from pathlib import Path


import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.resource_node_scan_cache import ResourceNodeScanCache  # noqa: E402


class ResourceNodeScanCacheTests(unittest.TestCase):
    def test_same_size_same_mtime_replacement_is_reparsed_by_content_hash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset = root / "Node_settings.uasset"
            asset.write_bytes(b"v1")
            original = asset.stat()
            cache_path = root / "cache.json"
            calls: list[bytes] = []

            def extract(path: Path):
                payload = path.read_bytes()
                calls.append(payload)
                return {"version": payload.decode("ascii")}

            cache = ResourceNodeScanCache(cache_path)
            cache.get_or_extract(asset, extract)
            cache.flush()
            asset.write_bytes(b"v2")
            os.utime(asset, ns=(original.st_atime_ns, original.st_mtime_ns))

            reloaded = ResourceNodeScanCache(cache_path)
            result, hit = reloaded.get_or_extract(asset, extract)

        self.assertFalse(hit)
        self.assertEqual(result["version"], "v2")
        self.assertEqual(calls, [b"v1", b"v2"])

    def test_extractor_version_change_invalidates_the_complete_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset = root / "Node_settings.uasset"
            asset.write_bytes(b"same")
            cache_path = root / "cache.json"
            calls = 0

            def extract(_path: Path):
                nonlocal calls
                calls += 1
                return {"call": calls}

            first = ResourceNodeScanCache(cache_path, extractor_version="parser/v1")
            first.get_or_extract(asset, extract)
            first.flush()
            second = ResourceNodeScanCache(cache_path, extractor_version="parser/v2")
            result, hit = second.get_or_extract(asset, extract)

        self.assertFalse(hit)
        self.assertEqual(result["call"], 2)
        self.assertEqual(second.load_status, "VERSION_MISMATCH_IGNORED")

    def test_unchanged_asset_is_reused_and_changed_asset_is_reparsed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset = root / "Node_settings.uasset"
            asset.write_bytes(b"v1")
            cache_path = root / "node-cache.json"
            calls: list[bytes] = []

            def extract(path: Path):
                payload = path.read_bytes()
                calls.append(payload)
                return {"objectPath": "/Game/Node", "version": payload.decode("ascii")}

            cache = ResourceNodeScanCache(cache_path)
            first, first_hit = cache.get_or_extract(asset, extract)
            second, second_hit = cache.get_or_extract(asset, extract)
            cache.flush()

            reloaded = ResourceNodeScanCache(cache_path)
            third, third_hit = reloaded.get_or_extract(asset, extract)
            asset.write_bytes(b"version-two")
            fourth, fourth_hit = reloaded.get_or_extract(asset, extract)

        self.assertEqual(first["version"], "v1")
        self.assertEqual(second["version"], "v1")
        self.assertEqual(third["version"], "v1")
        self.assertEqual(fourth["version"], "version-two")
        self.assertFalse(first_hit)
        self.assertTrue(second_hit)
        self.assertTrue(third_hit)
        self.assertFalse(fourth_hit)
        self.assertEqual(calls, [b"v1", b"version-two"])

    def test_refresh_bypasses_a_valid_cached_entry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset = root / "Node_settings.uasset"
            asset.write_bytes(b"same")
            calls = 0

            def extract(_path: Path):
                nonlocal calls
                calls += 1
                return {"call": calls}

            cache = ResourceNodeScanCache(root / "cache.json", refresh=True)
            first, first_hit = cache.get_or_extract(asset, extract)
            second, second_hit = cache.get_or_extract(asset, extract)

        self.assertEqual((first["call"], second["call"]), (1, 2))
        self.assertFalse(first_hit)
        self.assertFalse(second_hit)


if __name__ == "__main__":
    unittest.main()
