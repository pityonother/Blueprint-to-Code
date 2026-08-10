from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from arkdev_mcp.tasking.renderer import render_patch_plan  # noqa: E402
from arkdev_mcp.tasking.store import TaskStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render one local Blueprint Patch Plan as Markdown."
    )
    parser.add_argument("--task", required=True, help="Opaque task:// handle")
    parser.add_argument("--plan", required=True, help="Opaque patch-plan:// handle")
    parser.add_argument(
        "--task-root",
        type=Path,
        default=ROOT / ".blueprint-tasks",
        help=argparse.SUPPRESS,
    )
    arguments = parser.parse_args()
    plan = TaskStore(arguments.task_root).load_plan(arguments.task, arguments.plan)
    print(render_patch_plan(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
