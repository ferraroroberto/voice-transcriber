"""Download a prebuilt whisper.cpp release into `vendor/whisper.cpp/`.

Mirrors what `ferraroroberto/local-llm-hub` does for its `whisper`
backend, trimmed to a single-purpose installer: one binary, one model
location. On Windows we auto-detect whether an NVIDIA/CUDA GPU is present:
if yes, we grab the CUDA build (CUDA DLLs bundled, no Toolkit needed);
if no, we fall back to the plain CPU build so it works on any PC including
AMD/Intel-only laptops. Pass --cpu to force CPU mode regardless, or --from to
copy a working install from another folder instead of downloading.

Usage:
    python scripts/install_whisper_cpp.py
    python scripts/install_whisper_cpp.py --force
    python scripts/install_whisper_cpp.py --cpu           # force CPU-only build
    python scripts/install_whisper_cpp.py --cuda 12.4.0   # prefer a specific CUDA version
    python scripts/install_whisper_cpp.py --from <folder> # copy the folder holding whisper-server
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
from typing import List, Optional, Sequence, Tuple

# `python scripts/install_whisper_cpp.py` already puts this script's own
# directory on sys.path[0]; the explicit insert covers loading by path
# (e.g. a test using importlib.util.spec_from_file_location), which doesn't.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _no_window import no_window_kwargs  # noqa: E402

from src import runtime_files  # noqa: E402  (_no_window put the repo root on sys.path)

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIR = PROJECT_ROOT / "vendor" / "whisper.cpp"
BINARY_NAME = "whisper-server.exe" if sys.platform == "win32" else "whisper-server"
# Upstream attaches the binaries to its build releases, while the version tag
# that `releases/latest` returns carries none, so the picker walks the list.
RELEASE_WINDOW = 30
RELEASES_API = f"https://api.github.com/repos/ggml-org/whisper.cpp/releases?per_page={RELEASE_WINDOW}"

# Newest CUDA first — the prebuilt asset must match the driver on the host.
# whisper.cpp ships a zip per major CUDA toolchain; any should work with a
# sufficiently recent NVIDIA driver since CUDA is forward-compatible.
WIN_CUDA_VERSIONS = ("12.4.0", "12.2.0", "11.8.0")
# Both upstream naming schemes: releases in the window carry either one.
WIN_CUDA_NAMES = ("whisper-bin-win-cuda-{}-x64.zip", "whisper-cublas-{}-bin-x64.zip")
# Exact names, so a renamed CUDA zip can never pass for a CPU build.
WIN_CPU_NAMES = ("whisper-bin-x64.zip", "whisper-blas-bin-x64.zip")

SERVER_NAMES = ("whisper-server", "whisper-server.exe", "server", "server.exe")


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
            **no_window_kwargs(),
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
    parser.add_argument("--cuda", default=None, help="Prefer a specific CUDA version (e.g. 12.4.0)")
    parser.add_argument(
        "--from", dest="source", type=Path, default=None,
        help="Copy a working install from this folder (the one holding whisper-server) instead of downloading",
    )
    args = parser.parse_args()

    if not args.force and _already_installed():
        log.info("whisper-server already installed at %s", VENDOR_DIR / BINARY_NAME)
        if args.source:
            log.info("--from ignored; add --force to replace the installed copy")
        return 0

    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    if args.source:
        if not _copy_install(args.source):
            return 1
    else:
        asset = _pick_asset(preferred_cuda=args.cuda, force_cpu=args.cpu)
        if asset is None:
            return 1

        name = asset["name"]
        log.info("Downloading %s", name)
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / name
            _download(asset["browser_download_url"], archive)
            log.info("Archive layout: %s", _archive_layout(archive))
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
    return bin_path.exists() and _runs(bin_path)


def _runs(binary: Path) -> bool:
    try:
        result = subprocess.run(
            [str(binary), "--help"],
            timeout=10,
            capture_output=True,
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        log.warning("%s --help did not finish within 10 s", binary)
        return False
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("%s could not be started: %s", binary, exc)
        return False
    # whisper.cpp exits 1 on --help but still prints usage, which is the
    # shape we want for "the binary is runnable".
    if result.returncode not in (0, 1):
        log.warning("%s --help exited with code %s", binary, result.returncode)
        return False
    return True


def _copy_install(source: Path) -> bool:
    """Copy the server, its libraries and any ggml model from `source`.

    Nothing is copied unless the source binary runs where it is.
    """
    source = source.expanduser().resolve()
    binary = source / BINARY_NAME
    if not source.is_dir():
        log.error("--from %s is not a folder", source)
        return False
    if not binary.is_file():
        log.error(
            "--from %s holds no %s; point it at the folder that does "
            "(usually <other install>/vendor/whisper.cpp)", source, BINARY_NAME,
        )
        return False
    if not _runs(binary):
        log.error("--from %s: its %s does not run, nothing copied", source, BINARY_NAME)
        return False

    files = [p for p in source.iterdir() if p.is_file()]
    models = sorted((source / "models").glob("ggml-*.bin"))
    log.info(
        "Copying from %s: %d files beside the server, models: %s",
        source, len(files), ", ".join(m.name for m in models) or "none",
    )
    for item in (*files, *models):
        target = VENDOR_DIR / item.relative_to(source)
        runtime_files.make_dirs(target.parent)
        runtime_files.copy_file(item, target)
        shutil.copymode(item, target)  # keeps the execute bit off Windows
    return True


def _fetch_releases() -> List[dict]:
    with urllib.request.urlopen(RELEASES_API, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _first_asset(releases: List[dict], names: Sequence[str]) -> Optional[Tuple[str, dict]]:
    """The newest release carrying one of `names`, earlier names preferred."""
    for release in releases:
        assets = {asset["name"]: asset for asset in release.get("assets", [])}
        for name in names:
            if name in assets:
                return release["tag_name"], assets[name]
    return None


def _first_mac_asset(releases: List[dict]) -> Optional[Tuple[str, dict]]:
    for release in releases:
        for asset in release.get("assets", []):
            name = asset["name"]
            if (
                name.startswith("whisper-")
                and "arm64" in name
                # Upstream's Windows and Ubuntu builds are arm64 too.
                and "-win-" not in name
                and "ubuntu" not in name
                and name.endswith((".zip", ".tar.gz"))
            ):
                return release["tag_name"], asset
    return None


def _pick_asset(preferred_cuda: Optional[str], force_cpu: bool = False) -> Optional[dict]:
    if sys.platform not in ("win32", "darwin"):
        log.error(
            "whisper.cpp publishes no prebuilt server for this platform; build it "
            "yourself, then install it with --from <folder>."
        )
        return None

    releases = _fetch_releases()
    if sys.platform == "darwin":
        found = _first_mac_asset(releases)
    else:
        found = None
        use_cuda = not force_cpu and _has_cuda_gpu()
        if use_cuda:
            versions = (preferred_cuda, *WIN_CUDA_VERSIONS) if preferred_cuda else WIN_CUDA_VERSIONS
            found = _first_asset(releases, [n.format(v) for v in versions for n in WIN_CUDA_NAMES])
            if found:
                log.info("NVIDIA GPU detected — using the CUDA build")
            else:
                log.warning(
                    "NVIDIA GPU detected but none of the newest %d releases has a "
                    "matching CUDA asset; falling back to the CPU build.", len(releases),
                )
        else:
            log.info("Using the CPU build (%s)", "--cpu flag" if force_cpu else "no NVIDIA GPU detected")
        found = found or _first_asset(releases, WIN_CPU_NAMES)

    if found is None:
        log.error(
            "None of the newest %d whisper.cpp releases has a build for this platform "
            "(checked %s). Install a working copy with --from <folder> instead.",
            len(releases), RELEASES_API,
        )
        return None

    tag, asset = found
    log.info("Chosen release %s, asset %s", tag, asset["name"])
    return asset


def _archive_layout(archive: Path) -> str:
    """One line saying which folders the archive holds and where the server is."""
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
    elif archive.name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive, "r:gz") as tf:
            names = [m.name for m in tf.getmembers() if m.isfile()]
    else:
        return "unknown archive format"
    folders = sorted({n.rsplit("/", 1)[0] + "/" if "/" in n else "./" for n in names})
    server = next((n for n in names if n.rsplit("/", 1)[-1] in SERVER_NAMES), "not found")
    return f"{len(names)} files in {', '.join(folders)}; server: {server}"


def _download(url: str, dest: Path) -> None:
    with urllib.request.urlopen(url, timeout=120) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        downloaded = 0
        next_pct = 5
        with runtime_files.open_for_write(dest, "wb") as out:
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
            runtime_files.move(child, root / child.name)
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
            runtime_files.move(sibling, target)
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
