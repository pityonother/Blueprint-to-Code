#!/usr/bin/env python3
"""Compatibility entrypoint for the ARK DevKit / Unreal Blueprint translator.

The implementation now lives in the ``scripts/blueprint_translator`` package.
This wrapper keeps the original script path working for existing tests,
batch files, and user commands.
"""

from __future__ import annotations

import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from blueprint_translator.engine import *  # noqa: F401,F403,E402
from blueprint_translator.engine import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
