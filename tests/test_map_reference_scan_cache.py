import json
import os
import tempfile
import unittest
from pathlib import Path


import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_translator.map_reference_scan_cache import (  # noqa: E402
    FINGERPRINT_POLICY,
    MapReferenceScanCache,
    node_package_set_revision,
)


class MapReferenceScanCacheTests(unittest.TestCase):
    def test_reuses_exact_matched_packages_for_unchanged_map(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            map_path = root / "TheIsland.umap"
            map_path.write_bytes(b"map-v1")
            cache_path = root / "map-cache.json"
            calls = 0

            def scan(_path: Path):
                nonlocal calls
                calls += 1
                return {
                    "/game/nodes/metal_settings",
                    "/game/nodes/metal_settings_extra",
                }

            first = MapReferenceScanCache(
                cache_path,
                node_packages={"/Game/Nodes/Metal_settings"},
            )
            matched, hit = first.get_or_scan(map_path, scan)
            first.checkpoint()
            reopened = MapReferenceScanCache(
                cache_path,
                node_packages={"/GAME/NODES/METAL_SETTINGS"},
            )
            reused, reused_hit = reopened.get_or_scan(map_path, scan)

        self.assertFalse(hit)
        self.assertTrue(reused_hit)
        self.assertEqual(matched, {"/game/nodes/metal_settings"})
        self.assertEqual(reused, matched)
        self.assertEqual(calls, 1)
        self.assertEqual(reopened.coverage()["hits"], 1)

    def test_file_size_change_invalidates_one_map(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            map_path = root / "Genesis.umap"
            map_path.write_bytes(b"old")
            cache_path = root / "map-cache.json"
            packages = {"/game/nodes/old", "/game/nodes/new"}

            first = MapReferenceScanCache(cache_path, node_packages=packages)
            first.get_or_scan(map_path, lambda _path: {"/game/nodes/old"})
            first.checkpoint()
            map_path.write_bytes(b"new-longer")

            reopened = MapReferenceScanCache(cache_path, node_packages=packages)
            matched, hit = reopened.get_or_scan(
                map_path, lambda _path: {"/game/nodes/new"}
            )

        self.assertFalse(hit)
        self.assertEqual(matched, {"/game/nodes/new"})
        self.assertEqual(reopened.coverage()["invalidated"], 1)
        self.assertEqual(reopened.coverage()["misses"], 1)

    def test_mtime_only_change_invalidates_one_map(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            map_path = root / "Genesis2.umap"
            map_path.write_bytes(b"same-size")
            original = map_path.stat()
            cache_path = root / "map-cache.json"
            packages = {"/game/nodes/a"}

            first = MapReferenceScanCache(cache_path, node_packages=packages)
            first.get_or_scan(map_path, lambda _path: packages)
            first.checkpoint()
            os.utime(
                map_path,
                ns=(original.st_atime_ns, original.st_mtime_ns + 1_000_000_000),
            )

            reopened = MapReferenceScanCache(cache_path, node_packages=packages)
            _matched, hit = reopened.get_or_scan(map_path, lambda _path: packages)

        self.assertFalse(hit)
        self.assertEqual(reopened.coverage()["invalidated"], 1)

    def test_same_size_and_mtime_is_a_stat_hit_not_a_content_sha_check(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            map_path = root / "ScorchedEarth.umap"
            map_path.write_bytes(b"old")
            original = map_path.stat()
            cache_path = root / "map-cache.json"
            packages = {"/game/nodes/old", "/game/nodes/new"}

            first = MapReferenceScanCache(cache_path, node_packages=packages)
            first.get_or_scan(map_path, lambda _path: {"/game/nodes/old"})
            first.checkpoint()
            map_path.write_bytes(b"new")
            os.utime(map_path, ns=(original.st_atime_ns, original.st_mtime_ns))

            reopened = MapReferenceScanCache(cache_path, node_packages=packages)
            matched, hit = reopened.get_or_scan(
                map_path, lambda _path: {"/game/nodes/new"}
            )
            coverage = reopened.coverage()

        self.assertTrue(hit)
        self.assertEqual(matched, {"/game/nodes/old"})
        self.assertEqual(coverage["fingerprintPolicy"], FINGERPRINT_POLICY)
        self.assertFalse(coverage["contentSha256Verified"])
        self.assertEqual(coverage["cachePurpose"], "PERFORMANCE_ONLY")

    def test_node_package_set_revision_change_invalidates_complete_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            map_path = root / "Aberration.umap"
            map_path.write_bytes(b"map")
            cache_path = root / "map-cache.json"

            first = MapReferenceScanCache(
                cache_path, node_packages={"/game/nodes/a"}
            )
            first.get_or_scan(map_path, lambda _path: {"/game/nodes/a"})
            first.checkpoint()

            changed = MapReferenceScanCache(
                cache_path,
                node_packages={"/game/nodes/a", "/game/nodes/b"},
            )
            matched, hit = changed.get_or_scan(
                map_path, lambda _path: {"/game/nodes/a", "/game/nodes/b"}
            )

        self.assertEqual(changed.load_status, "CONTEXT_MISMATCH_IGNORED")
        self.assertEqual(changed.coverage()["invalidated"], 1)
        self.assertFalse(hit)
        self.assertEqual(matched, {"/game/nodes/a", "/game/nodes/b"})

    def test_extractor_version_change_invalidates_complete_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            map_path = root / "Extinction.umap"
            map_path.write_bytes(b"map")
            cache_path = root / "map-cache.json"
            packages = {"/game/nodes/a"}

            first = MapReferenceScanCache(
                cache_path,
                node_packages=packages,
                extractor_version="map-token-parser/v1",
            )
            first.get_or_scan(map_path, lambda _path: packages)
            first.checkpoint()

            changed = MapReferenceScanCache(
                cache_path,
                node_packages=packages,
                extractor_version="map-token-parser/v2",
            )

        self.assertEqual(changed.load_status, "CONTEXT_MISMATCH_IGNORED")
        self.assertEqual(changed.coverage()["invalidated"], 1)

    def test_refresh_bypasses_hits_without_calling_them_invalid(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            map_path = root / "Fjordur.umap"
            map_path.write_bytes(b"map")
            cache_path = root / "map-cache.json"
            packages = {"/game/nodes/a"}
            calls = 0

            def scan(_path: Path):
                nonlocal calls
                calls += 1
                return packages

            first = MapReferenceScanCache(cache_path, node_packages=packages)
            first.get_or_scan(map_path, scan)
            first.checkpoint()
            refreshed = MapReferenceScanCache(
                cache_path, node_packages=packages, refresh=True
            )
            _matched, hit = refreshed.get_or_scan(map_path, scan)

        self.assertFalse(hit)
        self.assertEqual(calls, 2)
        self.assertEqual(refreshed.coverage()["refreshBypasses"], 1)
        self.assertEqual(refreshed.coverage()["invalidated"], 0)

    def test_checkpoint_every_writes_atomic_valid_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache_path = root / "map-cache.json"
            packages = {"/game/nodes/a"}
            cache = MapReferenceScanCache(
                cache_path,
                node_packages=packages,
                checkpoint_every=2,
            )
            first_map = root / "Map1.umap"
            second_map = root / "Map2.umap"
            first_map.write_bytes(b"one")
            second_map.write_bytes(b"two")

            cache.get_or_scan(first_map, lambda _path: packages)
            self.assertFalse(cache_path.exists())
            cache.get_or_scan(second_map, lambda _path: packages)
            payload = json.loads(cache_path.read_text(encoding="utf-8"))

        self.assertEqual(len(payload["entries"]), 2)
        self.assertFalse(any(root.glob(".map-cache.json.*.tmp")))

    def test_corrupt_cache_is_ignored_and_replaced_at_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache_path = root / "map-cache.json"
            cache_path.write_text("{broken", encoding="utf-8")
            map_path = root / "LostIsland.umap"
            map_path.write_bytes(b"map")
            packages = {"/game/nodes/a"}

            cache = MapReferenceScanCache(cache_path, node_packages=packages)
            matched, hit = cache.get_or_scan(map_path, lambda _path: packages)
            cache.checkpoint()
            payload = json.loads(cache_path.read_text(encoding="utf-8"))

        self.assertEqual(cache.load_status, "INVALID_IGNORED")
        self.assertFalse(hit)
        self.assertEqual(matched, packages)
        self.assertEqual(payload["nodePackageSetRevision"], cache.node_package_set_revision)

    def test_node_package_revision_is_order_case_and_duplicate_stable(self):
        left = node_package_set_revision(
            ["/Game/Nodes/A", "/game/nodes/b", "/Game/Nodes/A"]
        )
        right = node_package_set_revision(["/GAME/NODES/B", "/game/nodes/a"])

        self.assertEqual(left, right)
        self.assertEqual(len(left), 64)


if __name__ == "__main__":
    unittest.main()
