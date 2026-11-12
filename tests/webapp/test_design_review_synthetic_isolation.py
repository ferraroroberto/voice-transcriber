"""The design-review synthetic instance must resolve every runtime-data path inside its state dir.

Launched from the primary checkout it listed the owner's real takes, because the
archive defaulted to the checkout's ``archive/`` folder whatever the data-dir
variable said. The paths are resolved in a fresh interpreter with the spawn
environment, so the unit conftest's in-process patch of the archive default can
not hide a leak.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.design_review_synthetic import _spawn_env
from src.no_window import NO_WINDOW

_RESOLVE = """
import json
from src import activity_log, archive
from src.runtime_data import runtime_db_path
print(json.dumps({
    "archive": str(archive.default_archive_dir()),
    "activity": str(runtime_db_path("voice-transcriber", "activity.sqlite3")),
}))
"""

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_synthetic_env_keeps_every_runtime_data_path_in_the_state_dir(tmp_path: Path) -> None:
    env = _spawn_env(tmp_path)
    for name in ("VT_ACTIVITY_DB_PATH", "FLEET_DATA_ROOT"):
        env.pop(name, None)

    result = subprocess.run(
        [sys.executable, "-c", _RESOLVE],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=60,
        creationflags=NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr

    resolved = json.loads(result.stdout)
    state = tmp_path.resolve()
    for label, raw in resolved.items():
        path = Path(raw).resolve()
        assert state in path.parents, f"{label} resolves outside the state dir: {path}"
