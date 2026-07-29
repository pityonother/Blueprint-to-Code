import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import diagnose_blueprint_tool as diagnose  # noqa: E402


class DiagnoseBlueprintToolTests(unittest.TestCase):
    def test_launcher_manifest_success_does_not_warn_about_missing_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            content_root = Path(temp_dir) / "ARKDevkit" / "Projects" / "ShooterGame" / "Content"
            (content_root / "PrimalEarth").mkdir(parents=True)
            checks: list[dict[str, object]] = []
            with (
                patch.object(
                    diagnose,
                    "DEVKIT_CONTENT_ROOT_FILE",
                    Path(temp_dir) / "missing-devkit-content-root.txt",
                ),
                patch.object(
                    diagnose,
                    "candidate_content_roots",
                    return_value=[("Epic Games Launcher manifest", content_root)],
                ),
            ):
                selected = diagnose.check_devkit_content(checks, [])

        config_check = next(
            item for item in checks if item["name"] == "devkit_content_root.txt"
        )
        self.assertEqual(selected, ("Epic Games Launcher manifest", content_root))
        self.assertEqual(config_check["status"], "info")


if __name__ == "__main__":
    unittest.main()
