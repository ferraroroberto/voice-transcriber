"""FastAPI webapp — mobile-first voice transcriber.

Routes:

    GET  /                          → static/index.html
    GET  /static/{file}             → CSS / JS / mobileconfig
    GET  /healthz                   → liveness probe (used by tray)
    GET  /install-ca                → iOS .mobileconfig (Phase 3)

    GET  /api/config                → current webapp_config.json
    POST /api/config                → patch + persist
    GET  /api/status                → whisper + LLM hub reachability

    POST /api/sessions              → create new session
    POST /api/sessions/{id}/upload  → single-shot upload + transcribe (Phase 2)
    POST /api/sessions/{id}/chunk   → append a chunk (Phase 4 streaming)
    POST /api/sessions/{id}/finish  → close + transcode + transcribe (Phase 4)
    POST /api/sessions/{id}/polish  → run polish on the transcript
    POST /api/polish-text           → polish pasted text (creates text-only session)
    POST /api/save-text             → save pasted text to history (no polish)
    POST /api/sessions/{id}/retranscribe → re-run whisper on a saved take
    GET  /api/sessions              → list (newest first)
    DELETE /api/sessions            → cleanup all
    DELETE /api/sessions/older-than/{days} → cleanup old

The lifespan hook prunes sessions older than the configured retention
window on every boot, matching the user's expectation that startup is
when "the app cleans the history".
"""

from __future__ import annotations

# Standard library imports
import asyncio
import hmac
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

# Third-party imports
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from src.app_config import resolve_iso
from src import (
    LANGUAGE_MODE_LABELS,
    TranscriptionClient,
    TranscriptionError,
    load_app_config,
)
from src.archive import SessionArchive
from src.polish import PolishClient, PolishError
from src.silence import is_silent, rms_dbfs_from_wav
from src.polish_prompts import (
    PolishPrompt,
    get_prompt,
    load_polish_prompts,
)
from src.webapp_config import (
    WebappConfig,
    load_webapp_config,
    update_webapp_config,
)
from src.whisper_server import WhisperServerManager

from .audio import (
    AudioToolMissing,
    AudioTranscodeError,
    find_ffmpeg,
    transcode_to_wav,
)
from .partial_worker import PartialWorker, encode_sse

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"

# Loopback addresses bypass the bearer-token gate so the tk window and
# local probes keep working without carrying the token. Tailscale and
# tunnel traffic both arrive with a non-loopback client IP and so must
# present the token.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

# Endpoints that must remain reachable without the token: liveness probes
# (/healthz), the iOS profile install (/install-ca), the page boot
# (/ + /static/*) so the JS can pick up the token from ?token= and
# attach it to subsequent API calls, and /api/login so a device with no
# token can swap a password for the bearer token.
_AUTH_EXEMPT_PREFIXES = ("/static/", "/healthz", "/install-ca")
_AUTH_EXEMPT_EXACT = frozenset({"/", "/healthz", "/install-ca", "/api/login"})


# Dedicated logger for password attempts — written to webapp/auth.log
# in addition to the normal stderr stream so failed attempts are easy
# to find without scrolling through full server logs.
auth_logger = logging.getLogger("vt.auth")
_AUTH_LOG_PATH = Path(__file__).resolve().parent.parent.parent / "webapp" / "auth.log"


def _ensure_auth_log_handler() -> None:
    if any(
        isinstance(h, logging.FileHandler)
        and Path(h.baseFilename).resolve() == _AUTH_LOG_PATH.resolve()
        for h in auth_logger.handlers
    ):
        return
    try:
        _AUTH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(_AUTH_LOG_PATH, encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        )
        auth_logger.addHandler(fh)
        auth_logger.setLevel(logging.INFO)
    except OSError as exc:
        logger.warning(f"⚠️  Could not open {_AUTH_LOG_PATH}: {exc}")


_ensure_auth_log_handler()


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Require Authorization: Bearer <token> on API endpoints.

    Behaviour:

    - If the configured token is empty, the middleware short-circuits
      and the webapp behaves exactly as it did before this feature
      landed. This is the default.
    - Loopback callers (127.0.0.1, ::1) always bypass — the tk window
      and any local scripts keep working without the token.
    - The page itself (`/`, `/static/*`) and probes (`/healthz`,
      `/install-ca`) are exempt so the JS can boot, read the token
      from `?token=...`, and attach it to subsequent API fetches.
    - Otherwise we accept the token from either an
      `Authorization: Bearer <token>` header or a `?token=<token>`
      query string (so the very first navigation from a tokenised
      URL still passes for any non-exempt path).
    """

    def __init__(self, app, get_token):
        super().__init__(app)
        self._get_token = get_token

    async def dispatch(self, request: Request, call_next):
        token = (self._get_token() or "").strip()
        if not token:
            return await call_next(request)

        client_host = request.client.host if request.client else ""
        if client_host in _LOOPBACK_HOSTS:
            return await call_next(request)

        path = request.url.path
        if path in _AUTH_EXEMPT_EXACT or any(
            path.startswith(p) for p in _AUTH_EXEMPT_PREFIXES
        ):
            return await call_next(request)

        presented = ""
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            presented = auth_header[7:].strip()
        if not presented:
            presented = request.query_params.get("token", "").strip()

        if presented and hmac.compare_digest(presented, token):
            return await call_next(request)

        return JSONResponse(
            status_code=401,
            content={"detail": "missing or invalid bearer token"},
            headers={"WWW-Authenticate": 'Bearer realm="voice-transcriber"'},
        )


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
    # Per-session rolling-transcription workers. Keyed by session_id;
    # populated lazily on the first chunk and cleaned up on /finish or
    # session delete.
    app.state.partial_workers: Dict[str, PartialWorker] = {}

    # ----------------------------------------------------- static routes

    if STATIC_DIR.exists():
        app.mount(
            "/static",
            StaticFiles(directory=str(STATIC_DIR)),
            name="static",
        )

    @app.get("/")
    async def index() -> FileResponse:
        index_path = STATIC_DIR / "index.html"
        if not index_path.exists():
            raise HTTPException(status_code=500, detail="index.html missing")
        return FileResponse(str(index_path))

    @app.get("/healthz")
    async def healthz() -> Dict[str, Any]:
        return {"ok": True, "service": "voice-transcriber-webapp"}

    @app.get("/install-ca")
    async def install_ca() -> FileResponse:
        """Serve the iOS .mobileconfig for one-tap CA install (Phase 3)."""
        profile = STATIC_DIR / "voice-transcriber-ca.mobileconfig"
        if not profile.exists():
            raise HTTPException(
                status_code=404,
                detail=(
                    "CA profile not generated yet. Run "
                    "`scripts/gen_ssl_cert.py` from the project root."
                ),
            )
        return FileResponse(
            str(profile),
            media_type="application/x-apple-aspen-config",
            filename="voice-transcriber-ca.mobileconfig",
        )

    # ------------------------------------------------------ config API

    @app.get("/api/config")
    async def get_config(request: Request) -> Dict[str, Any]:
        cfg: WebappConfig = request.app.state.webapp_config
        app_cfg = request.app.state.app_config
        prompts = load_polish_prompts()
        return {
            "polish_model_default": cfg.polish_model_default,
            "polish_models_available": cfg.polish_models_available,
            "polish_prompt_default": cfg.polish_prompt_default,
            "polish_prompts": [
                {
                    "id": p.id,
                    "label": p.label,
                    "description": p.description,
                    "system": p.system,
                }
                for p in prompts
            ],
            "history_retention_days": cfg.history_retention_days,
            "force_builtin_mic_default": cfg.force_builtin_mic_default,
            "preferred_mic_id": cfg.preferred_mic_id,
            # Latency-collapse knobs exposed read-only to the client so
            # the JS can decide whether to subscribe to SSE partials and
            # arm the client-side VAD auto-stop.
            "partial_interval_seconds": cfg.partial_interval_seconds,
            "rolling_transcription_enabled": cfg.partial_interval_seconds > 0,
            "vad_auto_stop_enabled": cfg.vad_auto_stop_enabled,
            "auto_stop_silence_ms": cfg.auto_stop_silence_ms,
            # Languages exposed in the picker — narrowed by
            # AppConfig.enabled_languages when set, otherwise the full
            # 99-language Whisper list. Sorted alphabetically by label so
            # the dropdown reads naturally. Each entry carries both the ISO
            # code (sent to the server) and the display label.
            "languages": sorted(
                [{"iso": iso, "label": label}
                 for iso, label in app_cfg.enabled_language_map().items()],
                key=lambda e: e["label"],
            ),
            "language_default": resolve_iso(app_cfg.language) or "en",
        }

    @app.post("/api/config")
    async def patch_config(request: Request) -> Dict[str, Any]:
        body = await request.json()
        allowed = {
            "polish_model_default",
            "polish_prompt_default",
            "force_builtin_mic_default",
            "preferred_mic_id",
            "history_retention_days",
            "vad_auto_stop_enabled",
            "auto_stop_silence_ms",
        }
        patch = {k: v for k, v in body.items() if k in allowed}
        try:
            new_cfg = update_webapp_config(**patch)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        request.app.state.webapp_config = new_cfg
        return {"ok": True, "config": _config_dict(new_cfg)}

    @app.post("/api/login")
    async def login(request: Request) -> Dict[str, Any]:
        """Swap a password for the bearer token.

        Used by the page when no token is in localStorage — typical on
        a fresh device or inside an iOS PWA whose storage is partitioned
        from Safari's. Failed attempts are logged with the client IP to
        webapp/auth.log so suspicious access is visible.
        """
        cfg: WebappConfig = request.app.state.webapp_config
        client_host = request.client.host if request.client else "?"
        if not cfg.auth_password:
            auth_logger.info(
                f"⚠️  Login attempt from {client_host} but no auth_password "
                "configured — password auth disabled"
            )
            raise HTTPException(
                status_code=503,
                detail="password auth not configured",
            )
        if not cfg.auth_token:
            auth_logger.info(
                f"⚠️  Login attempt from {client_host} but no auth_token "
                "configured — nothing to hand back"
            )
            raise HTTPException(
                status_code=503,
                detail="bearer token not configured",
            )
        body = await _maybe_json(request)
        presented = str(body.get("password") or "")
        if not presented or not hmac.compare_digest(presented, cfg.auth_password):
            auth_logger.warning(
                f"🚨 Failed password attempt from {client_host} "
                f"(presented: {len(presented)} chars)"
            )
            raise HTTPException(status_code=401, detail="bad password")
        auth_logger.info(f"🔓 Password login from {client_host}")
        return {"token": cfg.auth_token}

    @app.get("/api/status")
    async def status(request: Request) -> Dict[str, Any]:
        sm: WhisperServerManager = request.app.state.server_manager
        polish: PolishClient = request.app.state.polish_client
        whisper_status = sm.status()
        return {
            "whisper": {
                "running": whisper_status.running,
                "ownership": whisper_status.ownership,
                "base_url": whisper_status.base_url,
                "detail": whisper_status.detail,
            },
            "llm_hub": {
                "reachable": polish.is_reachable(),
                "base_url": polish.base_url,
            },
            "ffmpeg_present": find_ffmpeg(PROJECT_ROOT) is not None,
        }

    # ------------------------------------------------------ session API

    @app.post("/api/sessions")
    async def create_session(request: Request) -> Dict[str, Any]:
        body = await _maybe_json(request)
        archive: SessionArchive = request.app.state.archive
        app_cfg = request.app.state.app_config
        language = body.get("language") or app_cfg.language
        incognito = bool(body.get("incognito", False))
        session = archive.new_session(
            language=language,
            sample_rate=app_cfg.sample_rate,
            incognito=incognito,
        )
        return {
            "session_id": session.session_id,
            "folder": str(session.folder),
            "created_at": session.meta.created_at,
            "incognito": incognito,
        }

    @app.post("/api/sessions/{session_id}/upload")
    async def upload_and_transcribe(
        session_id: str,
        request: Request,
        file: UploadFile = File(...),
        language: Optional[str] = None,
        translate: bool = False,
    ) -> Dict[str, Any]:
        """Single-shot upload — receive whole audio blob, transcribe, return.

        Phase 4 adds chunked uploads via /chunk + /finish.
        """
        session = request.app.state.archive.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"unknown session {session_id}")

        # Persist the raw upload so it survives any subsequent failure.
        raw_bytes = await file.read()
        if not raw_bytes:
            raise HTTPException(status_code=400, detail="empty upload")

        raw_path = session.raw_path()
        raw_path.write_bytes(raw_bytes)
        session.meta.raw_bytes = len(raw_bytes)
        session.meta.raw_format = file.content_type or "audio/webm;codecs=opus"
        session.write_meta()

        return await _transcribe_session_payload(request, session, language, translate=translate)

    @app.post("/api/sessions/{session_id}/chunk")
    async def append_chunk(session_id: str, request: Request) -> Dict[str, Any]:
        """Append a streamed audio chunk to the session's raw file.

        Body is the raw chunk bytes (no multipart wrapping — the client
        sends the binary blob directly so latency stays low and the
        server can persist it to disk before the recording even ends).
        """
        session = request.app.state.archive.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"unknown session {session_id}")
        body = await request.body()
        if not body:
            return {"session_id": session_id, "raw_bytes": session.meta.raw_bytes}
        size = session.append_raw_chunk(body)
        ctype = request.headers.get("content-type")
        if ctype and not session.meta.raw_format:
            session.meta.raw_format = ctype
        # Don't rewrite meta.json on every chunk — too much I/O. /finish writes it.
        # Kick the rolling-transcription worker so the next pass picks
        # up the freshly-appended bytes. No-op when the feature is off
        # (partial_interval_seconds == 0) or when the worker can't be
        # spawned (e.g. silence-skip flow).
        _ensure_partial_worker(request.app, session).mark_dirty()
        return {"session_id": session_id, "raw_bytes": size}

    @app.get("/api/sessions/{session_id}/events")
    async def session_events(session_id: str, request: Request) -> StreamingResponse:
        """Server-Sent Events stream for one session's partial transcripts.

        The client opens this on record start and consumes ``partial``,
        ``polish_partial``, ``final``, and ``polish_final`` events as
        they arrive. Cloudflare passes SSE through cleanly without any
        special config. The middleware accepts the bearer token from
        the ``?token=`` query string so EventSource (no custom headers)
        still authenticates.
        """
        session = request.app.state.archive.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"unknown session {session_id}")
        worker = _ensure_partial_worker(request.app, session)

        async def event_gen():
            # Open with a comment so intermediaries flush the connection
            # immediately — some proxies buffer until the first message.
            yield ":ok\n\n"
            try:
                async for evt in worker.subscribe():
                    if await request.is_disconnected():
                        break
                    yield encode_sse(evt.kind, evt.payload)
            except asyncio.CancelledError:
                pass

        return StreamingResponse(
            event_gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @app.post("/api/sessions/{session_id}/finish")
    async def finish_session(
        session_id: str,
        request: Request,
        language: Optional[str] = None,
        translate: bool = False,
    ) -> Dict[str, Any]:
        """Close a chunked session, transcode, transcribe, return text.

        When rolling transcription is enabled and the latest partial
        already covers the full audio (no new bytes since the last
        pass), we skip the final whisper call and serve the partial.
        """
        session = request.app.state.archive.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"unknown session {session_id}")
        raw_path = session.raw_path()
        if not raw_path.exists() or raw_path.stat().st_size == 0:
            raise HTTPException(
                status_code=400,
                detail="no chunks received — nothing to transcribe",
            )
        # Sync the on-disk reality back into meta (chunk endpoint skips this).
        session.meta.raw_bytes = raw_path.stat().st_size
        body = await _maybe_json(request)
        duration = body.get("duration_seconds")
        if isinstance(duration, (int, float)):
            session.meta.duration_seconds = float(duration)
        session.write_meta()

        worker: Optional[PartialWorker] = (
            request.app.state.partial_workers.get(session_id)
        )
        # Skip final whisper when the rolling worker already has the
        # whole take covered. Even a one-byte difference is enough to
        # justify a final pass — Cloudflare can briefly buffer the last
        # chunk and we want the canonical transcript on disk.
        if (
            worker is not None
            and worker.partial_text
            and worker.last_bytes_at_partial == session.meta.raw_bytes
        ):
            text = worker.partial_text
            session.write_transcript(text)
            session.meta.language = language or session.meta.language
            session.write_meta()
            await worker.finalise(text)
            await _shutdown_partial_worker(request.app, session_id)
            return {
                "session_id": session.session_id,
                "transcript": text,
                "language": session.meta.language,
                "from_partial": True,
            }

        result = await _transcribe_session_payload(
            request, session, language, translate=translate,
        )
        # Broadcast the final transcript to any open SSE stream and
        # tear down the worker so subscribers exit cleanly.
        if worker is not None:
            await worker.finalise(result.get("transcript", "") or "")
            await _shutdown_partial_worker(request.app, session_id)
        return result

    @app.post("/api/sessions/{session_id}/retranscribe")
    async def retranscribe(
        session_id: str,
        request: Request,
        language: Optional[str] = None,
        translate: bool = False,
    ) -> Dict[str, Any]:
        """Re-run whisper on an existing raw audio file (crash-recovery flow)."""
        session = request.app.state.archive.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"unknown session {session_id}")
        if not session.raw_path().exists():
            raise HTTPException(
                status_code=404,
                detail="raw audio missing — nothing to re-transcribe",
            )
        return await _transcribe_session_payload(request, session, language, translate=translate)

    @app.post("/api/sessions/{session_id}/polish")
    async def polish_session(session_id: str, request: Request) -> Dict[str, Any]:
        body = await _maybe_json(request)
        cfg: WebappConfig = request.app.state.webapp_config
        model = _resolve_model(body.get("model"), cfg)
        prompt = _resolve_prompt(body.get("prompt_id"), cfg)

        session = request.app.state.archive.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"unknown session {session_id}")

        # The client may send the (possibly user-edited) transcript so the
        # archive matches what's on screen. Persist it before polishing.
        edited = body.get("transcript")
        if isinstance(edited, str) and edited.strip():
            session.write_transcript(edited)
            session.write_meta()
            transcript = edited
        else:
            transcript = session.read_transcript()
        if not transcript:
            raise HTTPException(
                status_code=400,
                detail="no transcript yet — record first",
            )

        polish_client: PolishClient = request.app.state.polish_client
        try:
            result = polish_client.polish(
                transcript, model=model, system=prompt.system,
            )
        except PolishError as exc:
            session.mark_polish_failed(model, str(exc), prompt_id=prompt.id)
            session.write_meta()
            # 424 (Failed Dependency) instead of 502 so the Cloudflare
            # tunnel passes the JSON body through to the browser. Cloudflare
            # rewrites any 5xx from origin into its own HTML error page,
            # which clobbers the rich "upstream <addr> unreachable: ..."
            # message the hub returns.
            raise HTTPException(status_code=424, detail=str(exc))

        session.write_polished(
            result.polished_text,
            model=result.model,
            request_payload=result.request_payload,
            response_payload=result.response_payload,
            prompt_id=prompt.id,
        )
        session.write_meta()
        return {
            "session_id": session.session_id,
            "polished": result.polished_text,
            "model": result.model,
            "prompt_id": prompt.id,
        }

    @app.post("/api/polish-text")
    async def polish_text(request: Request) -> Dict[str, Any]:
        """Polish arbitrary pasted text without a recording.

        Creates a text-only session (no raw audio) so the result lands in
        History alongside dictated takes.
        """
        body = await _maybe_json(request)
        cfg: WebappConfig = request.app.state.webapp_config
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            raise HTTPException(status_code=400, detail="text is required")
        model = _resolve_model(body.get("model"), cfg)
        prompt = _resolve_prompt(body.get("prompt_id"), cfg)
        language = body.get("language")
        if language is not None and not isinstance(language, str):
            language = None

        archive: SessionArchive = request.app.state.archive
        session = archive.new_session(language=language)
        session.write_transcript(text)
        session.write_meta()

        polish_client: PolishClient = request.app.state.polish_client
        try:
            result = polish_client.polish(text, model=model, system=prompt.system)
        except PolishError as exc:
            session.mark_polish_failed(model, str(exc), prompt_id=prompt.id)
            session.write_meta()
            # 424 (Failed Dependency) instead of 502 so the Cloudflare
            # tunnel passes the JSON body through to the browser. Cloudflare
            # rewrites any 5xx from origin into its own HTML error page,
            # which clobbers the rich "upstream <addr> unreachable: ..."
            # message the hub returns.
            raise HTTPException(status_code=424, detail=str(exc))

        session.write_polished(
            result.polished_text,
            model=result.model,
            request_payload=result.request_payload,
            response_payload=result.response_payload,
            prompt_id=prompt.id,
        )
        session.write_meta()
        return {
            "session_id": session.session_id,
            "polished": result.polished_text,
            "model": result.model,
            "prompt_id": prompt.id,
        }

    @app.post("/api/save-text")
    async def save_text(request: Request) -> Dict[str, Any]:
        """Save arbitrary pasted text as a history entry without polishing.

        Mirrors /api/polish-text but skips the LLM call — the text lands in
        the archive as if it had been dictated, so the user can polish it
        later from the History list (or just keep it as a record).
        """
        body = await _maybe_json(request)
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            raise HTTPException(status_code=400, detail="text is required")
        language = body.get("language")
        if language is not None and not isinstance(language, str):
            language = None

        archive: SessionArchive = request.app.state.archive
        session = archive.new_session(language=language)
        session.write_transcript(text)
        session.write_meta()
        return {
            "session_id": session.session_id,
            "transcript": text,
        }

    @app.get("/api/sessions/{session_id}/text")
    async def get_session_text(session_id: str, request: Request) -> Dict[str, Any]:
        """Return the full transcript and polished text for one session.

        The list endpoint only returns 200-char previews to keep the page
        light; this endpoint backs the history Copy buttons so the user
        gets the full text, not the truncated preview.
        """
        session = request.app.state.archive.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"unknown session {session_id}")
        return {
            "session_id": session.session_id,
            "transcript": session.read_transcript() or "",
            "polished": session.read_polished() or "",
        }

    @app.get("/api/sessions")
    async def list_sessions(
        request: Request, limit: int = 10, offset: int = 0,
    ) -> Dict[str, Any]:
        archive: SessionArchive = request.app.state.archive
        if limit < 1:
            limit = 10
        if offset < 0:
            offset = 0
        sessions = archive.list_sessions(limit=limit, offset=offset)
        total = archive.count_sessions()
        return {
            "sessions": [
                {
                    "session_id": s.session_id,
                    "created_at": s.meta.created_at,
                    "language": s.meta.language,
                    "transcript_chars": s.meta.transcript_chars,
                    "polish_model": s.meta.polish_model,
                    "polish_prompt_id": s.meta.polish_prompt_id,
                    "polish_succeeded": s.meta.polish_succeeded,
                    "raw_bytes": s.meta.raw_bytes,
                    "duration_seconds": s.meta.duration_seconds,
                    "transcript_preview": _preview(s.read_transcript(), 200),
                    "polished_preview": _preview(s.read_polished(), 200),
                }
                for s in sessions
            ],
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    @app.delete("/api/sessions")
    async def delete_all_sessions(request: Request) -> Dict[str, Any]:
        archive: SessionArchive = request.app.state.archive
        removed = archive.cleanup_all()
        # Stop every active worker — sessions they were tied to are gone.
        for sid in list(request.app.state.partial_workers.keys()):
            await _shutdown_partial_worker(request.app, sid)
        return {"removed": removed}

    @app.delete("/api/sessions/{session_id}")
    async def delete_one_session(
        session_id: str, request: Request
    ) -> Dict[str, Any]:
        archive: SessionArchive = request.app.state.archive
        if not archive.delete_session(session_id):
            raise HTTPException(status_code=404, detail=f"unknown session {session_id}")
        await _shutdown_partial_worker(request.app, session_id)
        return {"removed": session_id}

    @app.delete("/api/sessions/older-than/{days}")
    async def delete_old_sessions(
        days: int, request: Request
    ) -> Dict[str, Any]:
        if days < 1:
            raise HTTPException(status_code=400, detail="days must be >= 1")
        archive: SessionArchive = request.app.state.archive
        removed = archive.cleanup_older_than(days)
        return {"removed": removed}

    return app


# --------------------------------------------------------------- helpers


async def _maybe_json(request: Request) -> Dict[str, Any]:
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


async def _transcribe_session_payload(
    request: Request,
    session,
    language: Optional[str],
    translate: bool = False,
) -> Dict[str, Any]:
    """Shared finish path: transcode → whisper → write transcript → meta."""
    app_cfg = request.app.state.app_config
    raw_path = session.raw_path()
    wav_path = session.wav_path()

    try:
        transcode_to_wav(raw_path, wav_path, sample_rate=app_cfg.sample_rate)
    except AudioToolMissing as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except AudioTranscodeError as exc:
        session.meta.error = str(exc)
        session.write_meta()
        raise HTTPException(status_code=500, detail=f"transcode failed: {exc}")

    chosen_lang = language or session.meta.language or app_cfg.language
    iso = resolve_iso(chosen_lang)

    # Silence gate — skip whisper entirely on near-silent audio so it
    # can't hallucinate "Thanks for watching" on an empty take.
    cfg: WebappConfig = request.app.state.webapp_config
    dbfs = rms_dbfs_from_wav(wav_path)
    if is_silent(dbfs, cfg.silence_dbfs_threshold):
        session.write_transcript("")
        session.meta.language = chosen_lang
        session.meta.extra["silence_dbfs"] = round(dbfs, 1)
        session.write_meta()
        logger.info(
            f"🤫 Skipped whisper for {session.session_id}: "
            f"{dbfs:.1f} dBFS < {cfg.silence_dbfs_threshold} dBFS threshold"
        )
        return {
            "session_id": session.session_id,
            "transcript": "",
            "language": chosen_lang,
            "silent": True,
            "dbfs": round(dbfs, 1),
        }

    client: TranscriptionClient = request.app.state.transcription_client
    try:
        text = client.transcribe_file(wav_path, language=iso, translate=translate)
    except TranscriptionError as exc:
        session.meta.error = str(exc)
        session.write_meta()
        raise HTTPException(status_code=502, detail=str(exc))

    text = (text or "").strip()
    session.write_transcript(text)
    session.meta.language = chosen_lang
    session.write_meta()

    return {
        "session_id": session.session_id,
        "transcript": text,
        "language": chosen_lang,
    }


# _iso_language() removed — use src.app_config.resolve_iso instead.


def _preview(text: Optional[str], n: int) -> Optional[str]:
    if not text:
        return None
    text = text.strip().replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "…"


def _config_dict(cfg: WebappConfig) -> Dict[str, Any]:
    return {
        "polish_model_default": cfg.polish_model_default,
        "polish_models_available": cfg.polish_models_available,
        "polish_prompt_default": cfg.polish_prompt_default,
        "history_retention_days": cfg.history_retention_days,
        "force_builtin_mic_default": cfg.force_builtin_mic_default,
        "preferred_mic_id": cfg.preferred_mic_id,
    }


def _resolve_prompt(
    prompt_id: Optional[str],
    cfg: WebappConfig,
) -> PolishPrompt:
    """Pick the prompt the client asked for, falling back to config default
    and finally to the first available entry."""
    pid = prompt_id if isinstance(prompt_id, str) and prompt_id else None
    if not pid:
        pid = cfg.polish_prompt_default
    return get_prompt(pid)


def _resolve_model(model: Any, cfg: WebappConfig) -> str:
    """Pick the polish model the client asked for, falling back to the
    configured default. Reject anything not in ``polish_models_available``
    with HTTP 400 so a typo can't waste a 120-second hub timeout (and
    trip the Cloudflare tunnel's 100 s edge cutoff with an HTML error
    page that the frontend can't render usefully)."""
    candidate = model if isinstance(model, str) and model.strip() else cfg.polish_model_default
    if candidate not in cfg.polish_models_available:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown polish model {candidate!r}; "
                f"allowed: {cfg.polish_models_available}"
            ),
        )
    return candidate


def _ensure_partial_worker(app: FastAPI, session) -> PartialWorker:
    """Get-or-create the rolling-transcription worker for ``session``.

    Returns a worker even when rolling transcription is disabled — its
    ``mark_dirty`` and ``subscribe`` methods are cheap no-ops in that
    case (no background loop ever runs, no events ever fire). Keeping
    the handler call sites unconditional simplifies the code.
    """
    workers: Dict[str, PartialWorker] = app.state.partial_workers
    existing = workers.get(session.session_id)
    if existing is not None:
        return existing

    cfg: WebappConfig = app.state.webapp_config
    app_cfg = app.state.app_config
    interval = float(cfg.partial_interval_seconds or 0.0)

    transcription_client: TranscriptionClient = app.state.transcription_client

    def _resolve_iso_for_session() -> Optional[str]:
        chosen = session.meta.language or app_cfg.language
        return resolve_iso(chosen)

    async def _transcribe(wav_path: Path) -> str:
        iso = _resolve_iso_for_session()
        return await asyncio.to_thread(
            transcription_client.transcribe_file, wav_path, iso, False,
        )

    async def _transcode(src: Path, dst: Path) -> None:
        await asyncio.to_thread(
            transcode_to_wav, src, dst, app_cfg.sample_rate,
        )

    worker = PartialWorker(
        session=session,
        interval_seconds=interval if interval > 0 else 2.0,
        transcribe=_transcribe,
        transcode=_transcode,
    )
    workers[session.session_id] = worker
    if interval > 0:
        worker.start()
    return worker


async def _shutdown_partial_worker(app: FastAPI, session_id: str) -> None:
    worker = app.state.partial_workers.pop(session_id, None)
    if worker is not None:
        await worker.stop()


# Module-level app for `uvicorn app.webapp.server:app`.
app = create_app()
