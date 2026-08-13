"""Download a prebuilt whisper.cpp release into `vendor/whisper.cpp/`.

Mirrors what `ferraroroberto/local-llm-hub` does for its `whisper`
backend, trimmed to a single-purpose installer: one binary, one model
location. On Windows we auto-detect whether an NVIDIA/CUDA GPU is present:
if yes, we grab the cuBLAS build (CUDA DLLs bundled, no Toolkit needed);
if no, we fall back to the plain CPU build so it works on any PC including
AMD/Intel-only laptops. Pass --cpu to force CPU mode regardless.

Usage:
    python scripts/install_whisper_cpp.py
    python scripts/install_whisper_cpp.py --force
    python scripts/install_whisper_cpp.py --cpu           # force CPU-only build
    python scripts/install_whisper_cpp.py --cuda 12.4.0   # prefer a specific cuBLAS tag
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Iterable, List, Optional

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIR = PROJECT_ROOT / "vendor" / "whisper.cpp"
BINARY_NAME = "whisper-server.exe" if sys.platform == "win32" else "whisper-server"
RELEASE_API = "https://api.github.com/repos/ggerganov/whisper.cpp/releases/latest"

# Newest CUDA first — the prebuilt asset must match the driver on the host.
# whisper.cpp ships a zip per major CUDA toolchain; any should work with a
# sufficiently recent NVIDIA driver since CUDA is forward-compatible.
WIN_CUDA_PREFS = ("cublas-12.4.0", "cublas-12.2.0", "cublas-11.8.0")


def _has_cuda_gpu() -> bool:
    """Return True when an NVIDIA CUDA-capable GPU is likely present.

    Tries nvidia-smi first (works wherever NVIDIA drivers are installed),
    then falls back to a DLL probe on Windows.  No CUDA Toolkit required —
    only the driver-level runtime needs to be present.
    """
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return True
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        pass

    if sys.platform == "win32":
        # nvcuda.dll is present whenever an NVIDIA driver is installed, even
        # without the full CUDA Toolkit.
        import ctypes.util
        if ctypes.util.find_library("nvcuda"):
            return True

    return False


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="Reinstall even if already present")
    parser.add_argument("--cpu", action="store_true", help="Force CPU-only build (skip CUDA detection)")
    parser.add_argument("--cuda", default=None, help="Prefer a specific cuBLAS tag (e.g. 12.4.0)")
    args = parser.parse_args()

    if not args.force and _already_installed():
        log.info("whisper-server already installed at %s", VENDOR_DIR / BINARY_NAME)
        return 0

    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    asset = _pick_asset(preferred_cuda=args.cuda, force_cpu=args.cpu)
    if asset is None:
        log.error("No matching whisper.cpp release asset found for this platform.")
        return 1

    url = asset["browser_download_url"]
    name = asset["name"]
    log.info("Downloading %s", name)
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / name
        _download(url, archive)
        log.info("Extracting into %s", VENDOR_DIR)
        _extract(archive, VENDOR_DIR)

    _flatten_if_nested(VENDOR_DIR)
    _lift_binary_and_dlls(VENDOR_DIR)

    if not _already_installed():
        log.error("Post-install check failed — %s not runnable.", BINARY_NAME)
        return 1

    log.info("Installed whisper-server → %s", VENDOR_DIR / BINARY_NAME)
    return 0


# ------------------------------------------------------------------- helpers


def _already_installed() -> bool:
    bin_path = VENDOR_DIR / BINARY_NAME
    if not bin_path.exists():
        return False
    try:
        result = subprocess.run(
            [str(bin_path), "--help"], timeout=10, capture_output=True
        )
    except (OSError, subprocess.SubprocessError):
        return False
    # whisper.cpp exits 1 on --help but still prints usage, which is the
    # shape we want for "the binary is runnable".
    return result.returncode in (0, 1)


def _pick_asset(preferred_cuda: Optional[str], force_cpu: bool = False) -> Optional[dict]:
    with urllib.request.urlopen(RELEASE_API, timeout=60) as resp:
        release = json.loads(resp.read().decode("utf-8"))
    assets: List[dict] = release.get("assets", [])

    if sys.platform == "win32":
        use_cuda = not force_cpu and _has_cuda_gpu()

        if use_cuda:
            prefs: Iterable[str] = (
                (f"cublas-{preferred_cuda}", *WIN_CUDA_PREFS)
                if preferred_cuda
                else WIN_CUDA_PREFS
            )
            for tag in prefs:
                for asset in assets:
                    name = asset["name"]
                    if (
                        name.startswith("whisper-")
                        and tag in name
                        and "x64" in name
                        and name.endswith(".zip")
                    ):
                        log.info("NVIDIA GPU detected — using CUDA build (%s)", tag)
                        return asset
            log.warning("NVIDIA GPU detected but no matching cuBLAS asset found; falling back to CPU build.")

        # CPU-only fallback: any x64 Windows zip that is not a CUDA/cuBLAS build.
        for asset in assets:
            name = asset["name"]
            if (
                name.startswith("whisper-")
                and "x64" in name
                and name.endswith(".zip")
                and "cublas" not in name.lower()
            ):
                if not use_cuda:
                    reason = "--cpu flag" if force_cpu else "no NVIDIA GPU detected"
                    log.info("Using CPU build (%s): %s", reason, name)
                return asset

        return None

    if sys.platform == "darwin":
        for asset in assets:
            name = asset["name"]
            if (
                name.startswith("whisper-")
                and "arm64" in name
                and (name.endswith(".zip") or name.endswith(".tar.gz"))
            ):
                return asset
        return None

    # Linux: no prebuilt server binaries are published upstream. Users must
    # build whisper.cpp themselves — fall through with a helpful error.
    return None


def _download(url: str, dest: Path) -> None:
    with urllib.request.urlopen(url, timeout=120) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        downloaded = 0
        next_pct = 5
        with dest.open("wb") as out:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = int(downloaded * 100 / total)
                    if pct >= next_pct:
                        log.info("   %d%% (%d MB / %d MB)", pct, downloaded // (1024 * 1024), total // (1024 * 1024))
                        next_pct = pct + 5
    log.info("   done (%d MB)", downloaded // (1024 * 1024))


def _extract(archive: Path, dest: Path) -> None:
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dest)
    elif archive.name.endswith(".tar.gz") or archive.name.endswith(".tgz"):
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(dest)
    else:
        raise RuntimeError(f"Unsupported archive format: {archive.name}")


def _flatten_if_nested(root: Path) -> None:
    """If the archive unpacked everything into a single top-level subdir,
    move its contents up so the binary lands directly under `root`.
    """
    entries = [p for p in root.iterdir() if p.name != "models"]
    if len(entries) == 1 and entries[0].is_dir():
        nested = entries[0]
        for child in list(nested.iterdir()):
            shutil.move(str(child), str(root / child.name))
        nested.rmdir()


def _lift_binary_and_dlls(root: Path) -> None:
    """whisper.cpp release zips often put `whisper-server[.exe]` (or
    `server[.exe]`) inside a `bin/` folder alongside the CUDA DLLs. Move
    everything up to `root` so the manager's PATH hack (binary's parent
    dir on PATH) finds the DLLs, and rename `server` → `whisper-server`.
    """
    candidates = list(root.rglob("whisper-server")) + list(root.rglob("whisper-server.exe"))
    candidates += list(root.rglob("server")) + list(root.rglob("server.exe"))
    for candidate in candidates:
        if candidate.parent == root:
            continue
        bin_dir = candidate.parent
        for sibling in list(bin_dir.iterdir()):
            target = root / sibling.name
            if target.exists():
                continue
            shutil.move(str(sibling), str(target))
        try:
            bin_dir.rmdir()
        except OSError:
            pass  # not empty — leave it, we already lifted what we needed

    for plain in ("server", "server.exe"):
        src = root / plain
        if src.exists():
            want = root / BINARY_NAME
            if not want.exists():
                src.rename(want)


if __name__ == "__main__":
    sys.exit(main())
