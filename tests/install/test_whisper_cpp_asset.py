"""`scripts/install_whisper_cpp.py`: which release asset it picks, and `--from`.

`tests/fixtures/whisper_cpp_releases.json` is a trimmed recording of upstream's
release list: version tags with no assets (the first is what `releases/latest`
returns), and build releases carrying either CUDA naming scheme. Nothing here
reaches the network or runs a real binary.
"""

from __future__ import annotations

import importlib.util
import io
import json
import logging
import subprocess
import zipfile
from pathlib import Path
from typing import Any, List, Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASES = json.loads(
    (REPO_ROOT / "tests" / "fixtures" / "whisper_cpp_releases.json").read_text(encoding="utf-8")
)

CUDA_NEW = "whisper-bin-win-cuda-12.4.0-x64.zip"
CUDA_OLD = "whisper-cublas-12.4.0-bin-x64.zip"
CPU = "whisper-bin-x64.zip"


@pytest.fixture
def mod() -> Any:
    spec = importlib.util.spec_from_file_location(
        "install_whisper_cpp_under_test", REPO_ROOT / "scripts" / "install_whisper_cpp.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _release(tag: str) -> dict:
    return next(r for r in RELEASES if r["tag_name"] == tag)


def _serve(
    monkeypatch: pytest.MonkeyPatch, mod: Any, releases: List[dict], *,
    platform: str = "win32", gpu: bool = False,
) -> None:
    """Answer the GitHub API from `releases`; `releases/latest` is its first entry."""

    def fake_urlopen(url: str, timeout: float = 0) -> io.BytesIO:
        body = releases[0] if url.endswith("/latest") else releases
        return io.BytesIO(json.dumps(body).encode("utf-8"))

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(mod.sys, "platform", platform)
    monkeypatch.setattr(mod, "_has_cuda_gpu", lambda: gpu)


def _picked(mod: Any, force_cpu: bool = False) -> Optional[str]:
    asset = mod._pick_asset(preferred_cuda=None, force_cpu=force_cpu)
    return asset["name"] if asset else None


@pytest.mark.parametrize(
    ("releases", "gpu", "force_cpu", "expected"),
    [
        pytest.param(RELEASES, True, False, CUDA_NEW, id="assetless-latest-gpu"),
        pytest.param(RELEASES, False, False, CPU, id="assetless-latest-no-gpu"),
        pytest.param(RELEASES, True, True, CPU, id="assetless-latest-cpu-flag"),
        pytest.param([_release("b5907")], True, False, CUDA_NEW, id="new-name-gpu"),
        pytest.param([_release("b5907")], False, False, CPU, id="new-name-no-gpu"),
        pytest.param([_release("b5130")], True, False, CUDA_OLD, id="old-name-gpu"),
        pytest.param([_release("b5130")], False, False, CPU, id="old-name-no-gpu"),
    ],
)
def test_windows_pick(
    mod: Any, monkeypatch: pytest.MonkeyPatch,
    releases: List[dict], gpu: bool, force_cpu: bool, expected: str,
) -> None:
    _serve(monkeypatch, mod, releases, gpu=gpu)
    assert _picked(mod, force_cpu) == expected


def test_a_cuda_zip_never_stands_in_for_the_cpu_build(
    mod: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    cuda_only = [
        {**r, "assets": [a for a in r["assets"] if "cu" in a["name"]]} for r in [_release("b5907")]
    ]
    _serve(monkeypatch, mod, cuda_only)
    with caplog.at_level(logging.ERROR):
        assert _picked(mod) is None
    assert "None of the newest 1 whisper.cpp releases" in caplog.text


def test_the_chosen_release_is_logged(
    mod: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _serve(monkeypatch, mod, RELEASES)
    with caplog.at_level(logging.INFO):
        _picked(mod)
    assert f"Chosen release b5907, asset {CPU}" in caplog.text


def test_mac_never_gets_a_windows_or_ubuntu_arm64_build(
    mod: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(monkeypatch, mod, RELEASES, platform="darwin")
    assert _picked(mod) is None


def test_archive_layout_names_the_folder_and_server(mod: Any, tmp_path: Path) -> None:
    archive = tmp_path / CPU
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Release/whisper-server.exe", "")
        zf.writestr("Release/whisper.dll", "")
    assert mod._archive_layout(archive) == "2 files in Release/; server: Release/whisper-server.exe"


# ------------------------------------------------------------------- --from


@pytest.fixture
def sibling(mod: Any, tmp_path: Path) -> Path:
    folder = tmp_path / "sibling"
    (folder / "models").mkdir(parents=True)
    (folder / mod.BINARY_NAME).write_text("server", encoding="utf-8")
    (folder / "whisper.dll").write_text("library", encoding="utf-8")
    (folder / "models" / "ggml-large-v3-turbo.bin").write_text("model", encoding="utf-8")
    return folder


def _run_main(
    mod: Any, monkeypatch: pytest.MonkeyPatch, vendor: Path, source: Path, exit_code: int
) -> int:
    def fake_run(command: List[str], **_: Any) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(command, exit_code)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(mod, "VENDOR_DIR", vendor)
    monkeypatch.setattr(mod.sys, "argv", ["install_whisper_cpp.py", "--from", str(source)])
    return mod.main()


def test_from_copies_a_working_install(
    mod: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sibling: Path
) -> None:
    vendor = tmp_path / "vendor"
    assert _run_main(mod, monkeypatch, vendor, sibling, exit_code=0) == 0
    copied = sorted(p.relative_to(vendor).as_posix() for p in vendor.rglob("*") if p.is_file())
    assert copied == sorted([mod.BINARY_NAME, "whisper.dll", "models/ggml-large-v3-turbo.bin"])


@pytest.mark.parametrize(
    ("exit_code", "drop_binary", "message"),
    [
        pytest.param(3221225781, False, "does not run, nothing copied", id="binary-does-not-run"),
        pytest.param(0, True, "holds no", id="no-binary"),
    ],
)
def test_from_refuses_a_broken_source(
    mod: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sibling: Path,
    caplog: pytest.LogCaptureFixture, exit_code: int, drop_binary: bool, message: str,
) -> None:
    if drop_binary:
        (sibling / mod.BINARY_NAME).unlink()
    vendor = tmp_path / "vendor"
    with caplog.at_level(logging.ERROR):
        assert _run_main(mod, monkeypatch, vendor, sibling, exit_code) == 1
    assert message in caplog.text
    assert not list(vendor.rglob("*"))
