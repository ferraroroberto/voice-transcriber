"""This app's `[install]` declaration in `.fleet.toml`, run through the vendored installer.

The installer itself is tested in project-scaffolding. These tests hold what is
specific here: the declared inputs create, keep and top up the right files, a
second run changes nothing, `-WhatIf` writes nothing and every file the
installer can create or back up is ignored by git. A fake runner stands in for
`subprocess.run`, so no venv is built and nothing is downloaded. The migration
declaration is held to the two things a reinstall relies on: everything it copies
is ignored by git, and every secret it compares is among what it copies.
"""

from __future__ import annotations

import datetime
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from src import installer, installer_migrate
from src.no_window import NO_WINDOW

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXED_NOW = datetime.datetime(2026, 1, 2, 3, 4, 5)

#: Files a person owns on a configured PC; the installer must never rewrite them.
REGISTRIES = ("vocabulary.json", "snippets.json", "speaker_blocklist.json", "disfluencies.json")


class FakeRunner:
    """Records each command and plays the venv's appearance; every command exits 0."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(command))
        if command[1:3] == ["-m", "venv"]:
            python = installer.venv_python(self.root)
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)


def _clone(tmp_path: Path) -> Path:
    """A fresh checkout's tracked inputs: the manifest, the requirements, the samples and the tracked configs."""
    root = tmp_path / "app"
    (root / "config").mkdir(parents=True)
    (root / "scripts").mkdir()
    for name in (".fleet.toml", "requirements.txt", "scripts/setup_assets.py"):
        shutil.copy(REPO_ROOT / name, root / name)
    for source in (REPO_ROOT / "config").iterdir():
        if source.name.endswith(".sample.json") or source.name in ("config.json", "polish_prompts.json", "whisper_server.yaml"):
            shutil.copy(source, root / "config" / source.name)
    return root


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _run(root: Path, *, what_if: bool = False) -> tuple[installer.Report, FakeRunner]:
    runner = FakeRunner(root)
    return installer.run(root, what_if=what_if, runner=runner, now=lambda: FIXED_NOW), runner


def test_fresh_clone_creates_the_ignored_configs_and_a_second_run_is_unchanged(tmp_path: Path) -> None:
    root = _clone(tmp_path)
    tracked = {name: (root / "config" / name).read_bytes() for name in ("config.json", "polish_prompts.json", "whisper_server.yaml")}

    first, runner = _run(root)
    assert not first.failed
    for name in ("webapp_config.json", *REGISTRIES):
        assert (root / "config" / name).is_file(), name
    # The dependency step is this app's asset fetch, run with the repo's venv python.
    assert any(call[-1] == "scripts/setup_assets.py" for call in runner.calls)
    # The tracked configs are not installer targets: they come back byte for byte.
    assert tracked == {name: (root / "config" / name).read_bytes() for name in tracked}

    before = _snapshot(root)
    second, _ = _run(root)
    assert not second.failed
    assert not second.changes
    assert _snapshot(root) == before


def test_what_if_writes_nothing(tmp_path: Path) -> None:
    root = _clone(tmp_path)
    before = _snapshot(root)

    report, runner = _run(root, what_if=True)

    assert report.changes
    assert runner.calls == []
    assert _snapshot(root) == before
    assert not (root / ".venv").exists()


def test_existing_settings_keep_their_values_gain_missing_keys_and_are_backed_up(tmp_path: Path) -> None:
    root = _clone(tmp_path)
    _run(root)
    settings = root / "config" / "webapp_config.json"
    snippets = root / "config" / "snippets.json"
    settings.write_text(
        json.dumps({"history_retention_days": 7, "polish_model_default": "custom", "local_only_key": True}),
        encoding="utf-8",
    )
    snippets.write_text('{"mine": "kept"}', encoding="utf-8")

    report, _ = _run(root)

    assert not report.failed
    topped = json.loads(settings.read_text(encoding="utf-8"))
    assert topped["history_retention_days"] == 7
    assert topped["polish_model_default"] == "custom"
    assert topped["local_only_key"] is True
    sample = json.loads((root / "config" / "webapp_config.sample.json").read_text(encoding="utf-8"))
    assert {key for key in sample if not key.startswith("_")} <= set(topped)
    backups = list((root / "config").glob("webapp_config.json.bak-*"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8"))["history_retention_days"] == 7
    # A registry is the owner's own list: not even a key is added.
    assert snippets.read_text(encoding="utf-8") == '{"mine": "kept"}'


def test_every_created_or_backed_up_file_is_gitignored() -> None:
    ignored = {line.strip() for line in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()}
    for name in ("webapp_config.json", *REGISTRIES):
        assert f"config/{name}" in ignored, name
    assert "config/*.bak-*" in ignored
    assert "vendor/" in ignored
    assert ".venv/" in ignored


def test_the_declared_checklist_paths_and_dependency_inputs_exist_in_the_repo() -> None:
    plan = installer.load_plan(REPO_ROOT)
    for name in plan.deps_inputs:
        assert (REPO_ROOT / name).is_file(), name
    assert (REPO_ROOT / "setup.bat").is_file()


def test_every_migrated_path_is_ignored_so_the_new_clone_stays_clean() -> None:
    # -MigrateFrom refuses a clone with untracked files, so a rerun needs every copy ignored.
    migration = installer_migrate.parse_migration(installer_migrate.read_fleet_toml(REPO_ROOT))
    for item in migration.copy:
        # A folder is probed through a file inside it: a clean checkout has no folder to match.
        probe = item if Path(item).suffix else f"{item}/probe"
        done = subprocess.run(
            ["git", "check-ignore", "-q", probe], cwd=REPO_ROOT, timeout=60, check=False, creationflags=NO_WINDOW
        )
        assert done.returncode == 0, item


def test_every_declared_secret_is_among_the_files_copied() -> None:
    migration = installer_migrate.parse_migration(installer_migrate.read_fleet_toml(REPO_ROOT))
    for secret in migration.secrets:
        assert any(secret == item or secret.startswith(f"{item}/") for item in migration.copy), secret


def test_a_migration_carries_vendor_so_setup_downloads_nothing(tmp_path: Path) -> None:
    old, new = tmp_path / "old", tmp_path / "new"
    assets = ("vendor/whisper.cpp/whisper-server.exe", "vendor/whisper.cpp/models/ggml-large-v3-turbo.bin")
    for name in assets:
        (old / name).parent.mkdir(parents=True, exist_ok=True)
        (old / name).write_bytes(b"working copy")
    new.mkdir()
    migration = installer_migrate.parse_migration(installer_migrate.read_fleet_toml(REPO_ROOT))
    report = installer_migrate.MigrationReport(mode="migrate", what_if=False)

    installer_migrate.copy_declared(old, new, migration, report, old_running=False)

    for name in assets:
        assert (new / name).is_file(), name
        assert (new / name).read_bytes() == b"working copy", name
