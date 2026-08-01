"""Stable identities and default paths for harvest catalog builds."""

from pathlib import Path

from ..contracts import YIELD_MODEL_VERSION

SCRIPT_DIR = Path(__file__).resolve().parents[3]
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_DEVKIT_ROOT = Path(r"C:\Program Files\Epic Games\ARKDevkit")
DEFAULT_RANKING_REPORT = PROJECT_ROOT / "analysis" / "harvest_rankings" / "harvest_ranking_all_resources.full.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "analysis" / "harvest_rankings" / "harvest_evaluation_catalog.json"
DEFAULT_AI_OUTPUT = DEFAULT_OUTPUT.with_name("harvest_evaluation_catalog.ai.json")
DEFAULT_SCAN_CACHE = DEFAULT_OUTPUT.with_name("creature_asset_scan_cache.json")
AI_SCHEMA = "ark-harvest-evaluation-catalog-ai/v2"
FORMULA_VERSION = YIELD_MODEL_VERSION
CREATURE_EXTRACTOR_VERSION = "ark-creature-attack-catalog/v3"
CREATURE_CANDIDATE_PATTERNS = ("*Character*.uasset", "*Char_BP*.uasset")
PREVIOUS_CREATURE_CANDIDATE_PATTERN = "*Character_BP*.uasset"
