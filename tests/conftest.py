"""Shared pytest fixtures.

Most tests in this suite work with isolated temp directories so the
real `config/` and `archive/` trees stay untouched. Fixtures here own
that isolation in one place.
"""

from __future__ import annotations

# Standard library imports
import json
from pathlib import Path
from typing import Iterator
from unittest.mock import MagicMock

# Third-party imports
import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def project_root() -> Path:
    """Absolute path to the repo root — useful when a test needs to
    read a real committed file (e.g. the sample config)."""
    return PROJECT_ROOT


@pytest.fixture
def sample_polish_payload() -> dict:
    """The committed `config/webapp_config.sample.json` parsed once per
    test. Lets sample-driven tests assert against the canonical first-
    run defaults without re-encoding them in Python."""
    sample = PROJECT_ROOT / "config" / "webapp_config.sample.json"
    return json.loads(sample.read_text(encoding="utf-8"))


@pytest.fixture
def tmp_config_dir(tmp_path: Path) -> Iterator[Path]:
    """Empty temp directory standing in for `config/`. Tests that need
    a sample.json inside it should write one explicitly via
    `write_sample_config`."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    yield cfg_dir


@pytest.fixture
def write_sample_config(tmp_config_dir: Path):
    """Helper that writes a `webapp_config.sample.json` into the temp
    config dir with the supplied payload. Tests use this to exercise
    the "sample is the source of truth" code path under controlled
    input."""

    def _write(payload: dict) -> Path:
        target = tmp_config_dir / "webapp_config.sample.json"
        target.write_text(json.dumps(payload), encoding="utf-8")
        return target

    return _write


# ---------------------------------------------------------------------------
# Webapp fixtures: isolated FastAPI app with all expensive deps stubbed out.
# ---------------------------------------------------------------------------

@pytest.fixture
def webapp_client(tmp_path: Path, monkeypatch):
    """Build a fresh FastAPI app whose state is wired entirely to temp
    directories + mocks. The real ``app = create_app()`` at the bottom
    of ``app/webapp/server.py`` imports unaffected because we work
    against a private copy here.

    Yields a tuple ``(client, app, state_overrides)`` where
    ``state_overrides`` is a dict the test can mutate to inject custom
    polish/transcription behaviour mid-test.
    """
    from fastapi.testclient import TestClient

    # Force the archive into tmp_path so list/get/delete tests don't
    # touch the real archive/ tree.
    from src import archive as archive_mod
    monkeypatch.setattr(
        archive_mod, "DEFAULT_ARCHIVE_DIR", tmp_path / "archive"
    )

    # Isolate the webapp config from the developer's real, gitignored
    # config/webapp_config.json. Without this, local overrides (e.g. a
    # different polish_model_default) leak into create_app() and make
    # sample-driven assertions environment-dependent — passing on a clean
    # CI box, failing on a machine that has run the webapp. Pointing at a
    # non-existent temp path makes load_webapp_config() fall back to
    # WebappConfig(), whose polish defaults come from the committed sample.
    from src import webapp_config as webapp_config_mod
    monkeypatch.setattr(
        webapp_config_mod, "DEFAULT_CONFIG_PATH",
        tmp_path / "webapp_config.json",
    )

    # Replace WhisperServerManager with a stub — the real one spawns
    # subprocesses, binds ports, and reads yaml off disk. None of that
    # is in scope for these tests.
    fake_wsm = MagicMock()
    fake_wsm.config = MagicMock(base_url="http://stub:8090")
    fake_wsm.status.return_value = MagicMock(
        running=False, ownership="none",
        base_url="http://stub:8090", detail="stubbed",
    )

    from app.webapp import server as server_mod
    monkeypatch.setattr(server_mod, "WhisperServerManager", lambda: fake_wsm)

    # Replace ffmpeg lookup so /api/status doesn't depend on a binary
    # being installed on the test machine.
    monkeypatch.setattr(server_mod, "find_ffmpeg", lambda _root: None)

    app = server_mod.create_app()

    # PolishClient & TranscriptionClient are real objects with mocked
    # underlying sessions, so request shaping still gets exercised.
    polish_mock = MagicMock()
    polish_mock.is_reachable.return_value = True
    polish_mock.base_url = "http://stub:8000"
    app.state.polish_client = polish_mock

    tx_mock = MagicMock()
    tx_mock.transcribe_file.return_value = "stubbed transcript"
    tx_mock.transcribe_wav_bytes.return_value = "stubbed transcript"
    app.state.transcription_client = tx_mock

    # The real webapp_config.json may have a bearer token configured —
    # but tests should run uncontested. Auth tests opt back in by
    # re-setting these fields directly on app.state.webapp_config.
    app.state.webapp_config.auth_token = ""
    app.state.webapp_config.auth_password = ""

    client = TestClient(app)
    overrides = {"polish": polish_mock, "transcription": tx_mock, "wsm": fake_wsm}
    return client, app, overrides

