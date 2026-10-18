"""The e2e harness must not let a spawned app write into the real runtime-data folder (#295).

`VT_USAGE_DB_PATH` names one file; the activity log resolves through
``runtime_db_path`` and leaked into the owner's folder until the harness set the
per-app directory variable. The "real" folder here is a temp stand-in reached via
``FLEET_DATA_ROOT``, so the test never touches actual runtime data.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.e2e.conftest import _spawn

_WRITE_ACTIVITY = (
    "from src import activity_log; "
    "activity_log.record_event('session_created', source='isolation-test')"
)


def test_spawned_app_writes_nothing_under_the_real_data_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real_root = tmp_path / "real-root"
    real_root.mkdir()
    monkeypatch.setenv("FLEET_DATA_ROOT", str(real_root))
    for name in ("VOICE_TRANSCRIBER_DATA_DIR", "VT_ACTIVITY_DB_PATH"):
        monkeypatch.delenv(name, raising=False)

    with (tmp_path / "child.log").open("w", encoding="utf-8") as log:
        proc = _spawn([sys.executable, "-c", _WRITE_ACTIVITY], log)
        try:
            assert proc.wait(timeout=60) == 0, "child failed to record an event"
        except subprocess.TimeoutExpired:
            proc.kill()
            raise

    assert list(real_root.rglob("*")) == [], "the spawned app wrote into the real data root"
