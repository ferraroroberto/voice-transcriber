"""FastAPI webapp — mobile-first voice transcriber.

Routes are split across ``app/webapp/routers/`` — one module per concern:

    GET  /                          → static/index.html         (misc)
    GET  /static/{file}             → CSS / JS / mobileconfig    (static mount)
    GET  /healthz                   → liveness probe            (misc)
    GET  /install-ca                → iOS .mobileconfig         (misc)
    GET  /api/version               → build identity            (misc)

    GET  /api/config                → current webapp_config.json (config)
    POST /api/config                → patch + persist           (config)
    GET  /api/status                → whisper + LLM hub probes   (config)

    POST /api/login                 → swap password for token   (auth)

    POST   /api/sessions[...]       → record / transcribe / polish / history
    POST   /api/polish-text         → polish pasted text         (sessions)
    POST   /api/save-text           → save pasted text           (sessions)

This module keeps only the wiring: ``create_app()``, the lifespan hook,
the static mount, and the module-level ``app`` for
``uvicorn app.webapp.server:app``.

The lifespan hook prunes sessions older than the configured retention
window on every boot, matching the user's expectation that startup is
when "the app cleans the history".
"""

from __future__ import annotations

# Standard library imports
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict

# Third-party imports
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from src import TranscriptionClient, load_app_config
from src.archive import SessionArchive
from src.polish import PolishClient
from src.static_versioning import BuildInfo
from src.webapp_config import WebappConfig, load_webapp_config
from src.whisper_server import WhisperServerManager

from app.webapp.audio import find_ffmpeg
from app.webapp.middleware import BearerTokenMiddleware
from app.webapp.partial_worker import PartialWorker
from app.webapp.routers import auth, config, misc, sessions
from app.webapp.routers._helpers import PROJECT_ROOT, STATIC_DIR

logger = logging.getLogger(__name__)

# Build identity, computed once at import — the tray restarts on every
# code edit, so a fresh process always reflects the deployed code.
BUILD_INFO = BuildInfo(STATIC_DIR, PROJECT_ROOT)

# Icons + manifest revalidate daily — they almost never change but we
# don't want a year of staleness either. Hash-stamped assets (.js / .css)
# get a one-year immutable cache, handled per-suffix in CachingStaticFiles.
_DAILY_ASSETS = frozenset({
    "manifest.webmanifest",
    "favicon.ico",
    "icon-180.png",
    "icon-512.png",
    "icon-512-maskable.png",
})

_IMMUTABLE_CACHE = "public, max-age=31536000, immutable"


class CachingStaticFiles(StaticFiles):
    """``StaticFiles`` with per-file ``Cache-Control`` + JS import stamping.

    Starlette's mount serves every file with only ``ETag`` /
    ``Last-Modified``, leaving iOS Safari free to heuristic-cache. This
    subclass stamps an explicit policy keyed on the suffix, and rewrites
    the ``import './x.js'`` URLs inside every ``.js`` module with a
    content hash so a stale module can never be served — the hashed URL
    changes on every edit.
    """

    def __init__(self, *, directory: str, build_info: BuildInfo) -> None:
        super().__init__(directory=directory)
        self._build_info = build_info

    def file_response(self, full_path, *args, **kwargs):  # type: ignore[override]
        path = Path(full_path)
        suffix = path.suffix.lower()

        if suffix == ".js":
            # Rewrite the module graph's `import './x.js'` URLs with a
            # content hash, then long-cache — the hashed URL is the
            # cache key, so an edit invalidates it for free.
            try:
                body = path.read_text(encoding="utf-8")
            except OSError:
                return super().file_response(full_path, *args, **kwargs)
            return Response(
                content=self._build_info.rewrite_js_imports(body),
                media_type="text/javascript",
                headers={"Cache-Control": _IMMUTABLE_CACHE},
            )

        response = super().file_response(full_path, *args, **kwargs)
        if suffix == ".css":
            response.headers["Cache-Control"] = _IMMUTABLE_CACHE
        elif path.name.lower() in _DAILY_ASSETS:
            response.headers["Cache-Control"] = "public, max-age=86400"
        return response


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Startup: prune archive older than retention window. Shutdown: close clients."""
    cfg: WebappConfig = app.state.webapp_config
    archive: SessionArchive = app.state.archive
    try:
        removed = archive.cleanup_older_than(cfg.history_retention_days)
        if removed:
            logger.info(f"🧹 Pruned {removed} old sessions on boot")
    except Exception as exc:  # noqa: BLE001 — never block startup
        logger.warning(f"⚠️  Archive prune failed: {exc}")

    if find_ffmpeg(PROJECT_ROOT) is None:
        logger.warning(
            "⚠️  ffmpeg not found on PATH or in vendor/ffmpeg/. "
            "Webm/opus uploads will fail until you install it. "
            "Suggested: winget install Gyan.FFmpeg"
        )

    yield

    try:
        app.state.transcription_client.close()
    except Exception:
        pass
    try:
        app.state.polish_client.close()
    except Exception:
        pass


def create_app() -> FastAPI:
    """Build the FastAPI app — wired with all dependencies."""
    app_config = load_app_config()
    webapp_cfg = load_webapp_config()
    server_manager = WhisperServerManager()
    archive = SessionArchive()
    transcription_client = TranscriptionClient(
        server_manager.config.base_url,
        translate_base_url=app_config.translate_base_url,
    )
    polish_client = PolishClient(webapp_cfg.llm_hub_url)

    auth.ensure_log_handler()

    app = FastAPI(
        title="Voice Transcriber",
        version="0.2.0",
        lifespan=_lifespan,
    )

    # Read the token from app.state on every request so a /api/config
    # patch that rotates it takes effect without a restart.
    app.add_middleware(
        BearerTokenMiddleware,
        get_token=lambda: getattr(app.state.webapp_config, "auth_token", ""),
    )

    # Stash dependencies on app.state so handlers can reach them without
    # a global. This also keeps the module import-side-effect-free, which
    # matters because the tray imports it to start uvicorn programmatically.
    app.state.app_config = app_config
    app.state.webapp_config = webapp_cfg
    app.state.server_manager = server_manager
    app.state.archive = archive
    app.state.transcription_client = transcription_client
    app.state.polish_client = polish_client
    app.state.build_info = BUILD_INFO
    # Per-session rolling-transcription workers. Keyed by session_id;
    # populated lazily on the first chunk and cleaned up on /finish or
    # session delete.
    app.state.partial_workers: Dict[str, PartialWorker] = {}

    if STATIC_DIR.exists():
        app.mount(
            "/static",
            CachingStaticFiles(directory=str(STATIC_DIR), build_info=BUILD_INFO),
            name="static",
        )

    app.include_router(misc.router)
    app.include_router(config.router)
    app.include_router(auth.router)
    app.include_router(sessions.router)

    logger.info(
        f"ℹ️  webapp build {BUILD_INFO.git_sha} "
        f"(app.js {BUILD_INFO.asset_hashes.get('app.js')}) "
        f"built {BUILD_INFO.built_at}"
    )

    return app


# Module-level app for `uvicorn app.webapp.server:app`.
app = create_app()
