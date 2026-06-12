"""Session API — recording lifecycle, transcription, polish, history.

The ``/api/sessions*`` family plus the two text-only entry points
(``/api/polish-text``, ``/api/save-text``). The shared transcribe path
and the rolling-transcription worker helpers live at the bottom.
"""

from __future__ import annotations

# Standard library imports
import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# Third-party imports
from fastapi import APIRouter, File, FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from src.app_config import resolve_iso
from src import TranscriptionClient, TranscriptionError
from src.archive import SessionArchive, parse_created_at
from src.polish import PolishClient, PolishError
from src.polish_prompts import PolishPrompt, get_prompt
from src.silence import is_silent_wav
from src.webapp_config import WebappConfig

from app.webapp.audio import (
    AudioToolMissing,
    AudioTranscodeError,
    transcode_to_wav,
)
from app.webapp.partial_worker import PartialWorker, encode_sse
from app.webapp.routers._helpers import maybe_json

logger = logging.getLogger(__name__)

router = APIRouter()


# A caller-supplied source label is free text, written to meta.json and
# surfaced as a History badge. Keep it short and trimmed so a stray blob
# can't bloat the row; an absent/blank value falls back to the generic
# "api" tag (a consumer that didn't self-identify).
_MAX_SOURCE_LEN = 40
_DEFAULT_API_SOURCE = "api"


def _clean_source(value: Any) -> Optional[str]:
    """Normalise a caller-supplied ``source`` to a short trimmed string.

    Returns ``None`` when the value is missing, not a string, or blank —
    the caller decides the default in that case.
    """
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    return trimmed[:_MAX_SOURCE_LEN]


@router.post("/api/sessions")
async def create_session(request: Request) -> Dict[str, Any]:
    body = await maybe_json(request)
    archive: SessionArchive = request.app.state.archive
    app_cfg = request.app.state.app_config
    language = body.get("language") or app_cfg.language
    incognito = bool(body.get("incognito", False))
    # The webapp's own record flow self-identifies as "webapp"; an
    # external consumer can pass its own label (e.g. "app-launcher").
    # A create with no source is an API caller that didn't self-identify.
    source = _clean_source(body.get("source")) or _DEFAULT_API_SOURCE
    session = archive.new_session(
        language=language,
        sample_rate=app_cfg.sample_rate,
        incognito=incognito,
        source=source,
    )
    return {
        "session_id": session.session_id,
        "folder": str(session.folder),
        "created_at": session.meta.created_at,
        "incognito": incognito,
        "source": source,
    }


@router.post("/api/sessions/{session_id}/upload")
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


@router.post("/api/sessions/{session_id}/chunk")
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


@router.get("/api/sessions/{session_id}/events")
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


@router.post("/api/sessions/{session_id}/finish")
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
    body = await maybe_json(request)
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


@router.post("/api/sessions/{session_id}/retranscribe")
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


@router.post("/api/sessions/{session_id}/polish")
async def polish_session(session_id: str, request: Request) -> Dict[str, Any]:
    body = await maybe_json(request)
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


@router.post("/api/polish-text")
async def polish_text(request: Request) -> Dict[str, Any]:
    """Polish arbitrary pasted text without a recording.

    Creates a text-only session (no raw audio) so the result lands in
    History alongside dictated takes.
    """
    body = await maybe_json(request)
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
    session = archive.new_session(language=language, source="webapp")
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


@router.post("/api/save-text")
async def save_text(request: Request) -> Dict[str, Any]:
    """Save arbitrary pasted text as a history entry without polishing.

    Mirrors /api/polish-text but skips the LLM call — the text lands in
    the archive as if it had been dictated, so the user can polish it
    later from the History list (or just keep it as a record).
    """
    body = await maybe_json(request)
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=400, detail="text is required")
    language = body.get("language")
    if language is not None and not isinstance(language, str):
        language = None

    archive: SessionArchive = request.app.state.archive
    session = archive.new_session(language=language, source="webapp")
    session.write_transcript(text)
    session.write_meta()
    return {
        "session_id": session.session_id,
        "transcript": text,
    }


@router.get("/api/sessions/{session_id}/text")
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


def _resolve_since(days: Optional[int], since: Optional[str]) -> Optional[datetime]:
    """Compute the window cutoff (naive local) from ``days``/``since``.

    Explicit ``since`` (ISO 8601) wins; otherwise ``days`` is taken as N
    days back from now. Returns ``None`` when neither is given (no
    window). Raises 400 on an unparseable ``since`` or non-positive
    ``days``.
    """
    if since is not None:
        cutoff = parse_created_at(since)
        if cutoff is None:
            raise HTTPException(
                status_code=400, detail=f"unparseable 'since' value: {since!r}"
            )
        return cutoff
    if days is not None:
        if days < 1:
            raise HTTPException(status_code=400, detail="'days' must be >= 1")
        return datetime.now() - timedelta(days=days)
    return None


@router.get("/api/sessions")
async def list_sessions(
    request: Request,
    limit: int = 10,
    offset: int = 0,
    days: Optional[int] = None,
    since: Optional[str] = None,
) -> Dict[str, Any]:
    archive: SessionArchive = request.app.state.archive
    if limit < 1:
        limit = 10
    if offset < 0:
        offset = 0
    window = _resolve_since(days, since)
    sessions = archive.list_sessions(limit=limit, offset=offset, since=window)
    total = archive.count_sessions()
    return {
        "sessions": [
            {
                "session_id": s.session_id,
                "created_at": s.meta.created_at,
                "language": s.meta.language,
                "source": s.meta.source,
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


@router.get("/api/sessions/transcripts")
async def list_session_transcripts(
    request: Request,
    days: Optional[int] = None,
    since: Optional[str] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Bulk full-transcript export over a date window — the mining path.

    Returns the full ``transcript`` text for every non-incognito session
    in the window in one call, avoiding the N+1 ``/text`` fetches a
    consumer would otherwise make. Backs the hub dictionary miner
    (voice-transcriber#60 / local-llm-hub#94). Empty transcripts are
    omitted. Newest-first.
    """
    archive: SessionArchive = request.app.state.archive
    window = _resolve_since(days, since)
    if limit is not None and limit < 1:
        limit = None
    sessions = archive.list_sessions(limit=limit, since=window)
    transcripts: List[Dict[str, Any]] = []
    for s in sessions:
        text = s.read_transcript()
        if text and text.strip():
            transcripts.append(
                {
                    "session_id": s.session_id,
                    "created_at": s.meta.created_at,
                    "transcript": text,
                }
            )
    return {"transcripts": transcripts, "count": len(transcripts)}


@router.delete("/api/sessions")
async def delete_all_sessions(request: Request) -> Dict[str, Any]:
    archive: SessionArchive = request.app.state.archive
    removed = archive.cleanup_all()
    # Stop every active worker — sessions they were tied to are gone.
    for sid in list(request.app.state.partial_workers.keys()):
        await _shutdown_partial_worker(request.app, sid)
    return {"removed": removed}


@router.delete("/api/sessions/{session_id}")
async def delete_one_session(
    session_id: str, request: Request
) -> Dict[str, Any]:
    archive: SessionArchive = request.app.state.archive
    if not archive.delete_session(session_id):
        raise HTTPException(status_code=404, detail=f"unknown session {session_id}")
    await _shutdown_partial_worker(request.app, session_id)
    return {"removed": session_id}


@router.delete("/api/sessions/older-than/{days}")
async def delete_old_sessions(
    days: int, request: Request
) -> Dict[str, Any]:
    if days < 1:
        raise HTTPException(status_code=400, detail="days must be >= 1")
    archive: SessionArchive = request.app.state.archive
    removed = archive.cleanup_older_than(days)
    return {"removed": removed}


# --------------------------------------------------------------- helpers


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
    silent, dbfs = is_silent_wav(wav_path, cfg.silence_dbfs_threshold)
    if silent:
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


def _preview(text: Optional[str], n: int) -> Optional[str]:
    if not text:
        return None
    text = text.strip().replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "…"


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
