"""One-shot ARK DevKit entrypoint for the read-only Wildcard Graph probe."""

from __future__ import annotations

import json
import importlib
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path


def _prepare_local_imports() -> str:
    """Prefer this worktree after another probe cached the same packages."""

    scripts_root = str(Path(__file__).resolve().parents[2])
    sys.path[:] = [entry for entry in sys.path if entry != scripts_root]
    sys.path.insert(0, scripts_root)
    for package_name in ("arkdev_scripting_probe", "devkit_exporters"):
        for module_name in tuple(sys.modules):
            if module_name == package_name or module_name.startswith(
                package_name + "."
            ):
                sys.modules.pop(module_name, None)
    importlib.invalidate_caches()
    return scripts_root


if __package__ in {None, ""}:
    _prepare_local_imports()

from arkdev_scripting_probe.contracts import canonical_json  # noqa: E402
from arkdev_scripting_probe.wc_graph_snapshot import (  # noqa: E402
    build_wc_graph_snapshot_result,
    validator_summary,
)


REQUEST_FILENAME = "wc-graph-snapshot-request.json"
RESULT_FILENAME = "wc-graph-snapshot-result.json"
SNAPSHOT_FILENAME = "wc-graph-snapshot.json"


def _load_request(path: Path) -> object:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(value) + "\n", encoding="utf-8")


def main() -> int:
    try:
        import unreal  # type: ignore[import-not-found]
    except Exception:
        unreal = None

    repo_root = Path(__file__).resolve().parents[3]
    output_root = repo_root / ".arkdev-probe"
    result_path = output_root / RESULT_FILENAME
    snapshot_path = output_root / SNAPSHOT_FILENAME
    for stale_path in (result_path, snapshot_path):
        try:
            stale_path.unlink(missing_ok=True)
        except TypeError:
            if stale_path.is_file():
                stale_path.unlink()
    request = _load_request(output_root / REQUEST_FILENAME)
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    result = build_wc_graph_snapshot_result(
        unreal,
        request,
        generated_at=generated_at,
    )
    _write_json(result_path, result)

    snapshot = result.get("snapshot")
    if isinstance(snapshot, Mapping):
        _write_json(snapshot_path, snapshot)
    elif snapshot_path.is_file():
        snapshot_path.unlink()

    try:
        summary = validator_summary(result)
    except ValueError:
        print("ARKDEV_WC_GRAPH_SNAPSHOT=CONTRACT_ERROR")
        return 1

    print("ARKDEV_WC_GRAPH_SNAPSHOT=COMPLETE")
    for key, value in summary.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        print("ARKDEV_WC_GRAPH_SNAPSHOT=ERROR")
        raise SystemExit(1) from None


__all__ = ["main"]
