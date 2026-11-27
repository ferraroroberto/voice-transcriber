"""A broken download is refused before anything opens it.

Both fetch scripts run their real `main()` with the network faked: an empty or
corrupt whisper.cpp archive extracts nothing, and an empty model is neither
accepted nor kept, so the rerun the error asks for really fetches it again.
"""

from __future__ import annotations

import importlib.util
import io
import logging
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = "whisper-bin-x64.zip"
MODEL = "ggml-large-v3-turbo.bin"


def _script(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", REPO_ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("Release/whisper-server.exe", b"A" * 4096)
    return buffer.getvalue()


def _bad_member() -> bytes:
    """A zip that opens and lists fine but whose one file fails its CRC."""
    return _zip_bytes().replace(b"A" * 4096, b"B" + b"A" * 4095)


@pytest.mark.parametrize(
    ("payload", "says"),
    [
        pytest.param(b"", "empty", id="empty"),
        pytest.param(_zip_bytes()[:200], "corrupt", id="truncated"),
        pytest.param(_bad_member(), "corrupt", id="bad-crc"),
        pytest.param(b"<html>rate limited</html>", "corrupt", id="not-a-zip"),
    ],
)
def test_broken_archive_is_refused_before_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    payload: bytes, says: str,
) -> None:
    mod = _script("install_whisper_cpp")
    vendor = tmp_path / "vendor"

    def fake_urlopen(url: str, timeout: float = 0) -> io.BytesIO:
        response = io.BytesIO(payload)
        response.headers = {}
        return response

    monkeypatch.setattr(mod, "VENDOR_DIR", vendor)
    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(mod, "_pick_asset", lambda **_: {"name": ARCHIVE, "browser_download_url": "https://x"})
    monkeypatch.setattr(mod.sys, "argv", ["install_whisper_cpp.py"])

    with caplog.at_level(logging.INFO):
        assert mod.main() == 1

    assert list(vendor.rglob("*")) == []
    error = next(r.getMessage() for r in caplog.records if r.levelno == logging.ERROR)
    assert ARCHIVE in error and says in error and "rerun setup" in error


def _fake_hf(monkeypatch: pytest.MonkeyPatch, payload: bytes) -> List[bool]:
    """Stand in for `huggingface_hub`; returns each call's `force_download`."""
    forced: List[bool] = []

    def download(repo_id: str, filename: str, local_dir: str, force_download: bool = False) -> str:
        forced.append(force_download)
        target = Path(local_dir) / filename
        target.write_bytes(payload)
        return str(target)

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(hf_hub_download=download))
    return forced


def test_empty_model_download_is_refused_and_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    mod = _script("download_model")
    _fake_hf(monkeypatch, b"")
    monkeypatch.setattr(mod.sys, "argv", ["download_model.py", "--dest", str(tmp_path)])

    assert mod.main() == 1

    assert not (tmp_path / MODEL).exists()
    error = next(r.getMessage() for r in caplog.records if r.levelno == logging.ERROR)
    assert MODEL in error and "empty" in error and "rerun setup" in error


def test_empty_model_left_on_disk_is_downloaded_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = _script("download_model")
    forced = _fake_hf(monkeypatch, b"weights")
    (tmp_path / MODEL).write_bytes(b"")
    monkeypatch.setattr(mod.sys, "argv", ["download_model.py", "--dest", str(tmp_path)])

    assert mod.main() == 0

    assert forced == [True]
    assert (tmp_path / MODEL).read_bytes() == b"weights"
