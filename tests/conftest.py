"""Tests run fully offline: local memory stand-in + heuristic reasoner, temp database."""

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="dejavu-tests-"))
shutil.copytree(ROOT / "data", _TMP / "data", ignore=shutil.ignore_patterns("*.db", "local_memory.json",
                                                                           "replay_results.json", "llm_cache.json"))
os.environ.update({
    "MEMORY_BACKEND": "local",
    "LLM_BACKEND": "offline",
    "DATA_DIR": str(_TMP / "data"),
    "DB_PATH": str(_TMP / "test.db"),
    "HINDSIGHT_API_KEY": "",
    "GROQ_API_KEY": "",
})

import json  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def history():
    return json.loads((ROOT / "data" / "history.json").read_text())


@pytest.fixture(scope="session")
def live():
    return json.loads((ROOT / "data" / "live.json").read_text())


@pytest.fixture()
def tmp_data(tmp_path):
    d = tmp_path / "data"
    shutil.copytree(ROOT / "data", d, ignore=shutil.ignore_patterns("*.db", "local_memory.json", "replay_results.json",
                                                                    "llm_cache.json"))
    return d
