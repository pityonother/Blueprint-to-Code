"""Cache capacities and mutable repository cache state."""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any


LAZY_CACHE_CAPACITY = 256
TOP_BASELINE_CACHE_CAPACITY = 1024
CREATURE_PAIR_CACHE_CAPACITY = 2048
V2_TIER_BASELINE_CACHE_CAPACITY = 1024
SPECIALTY_RESPONSE_CACHE_CAPACITY = 128
RUNTIME_OBSERVATION_CACHE_CAPACITY = 32


def initialize_repository_state(repository: Any) -> None:
    """Create all loader and LRU state in one ownership boundary."""

    repository._lock = threading.Lock()
    repository._catalog_signature = None
    repository._ranking_signature = None
    repository._evaluation_signature = None
    repository._catalog = None
    repository._ranking = None
    repository._evaluation = None
    repository._evaluation_engine = None
    repository._sqlite_signature = None
    repository._sqlite_source_signature = None
    repository._sqlite_catalog = None
    repository._runtime_dataset_signature = None
    repository._runtime_observation_cache = OrderedDict()
    repository._lazy_ranking_cache = OrderedDict()
    repository._top_baseline_cache = OrderedDict()
    repository._creature_pair_cache = OrderedDict()
    repository._v2_tier_baseline_cache = OrderedDict()
    repository._specialty_response_cache = OrderedDict()
