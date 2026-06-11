"""Dated session archive — every recording's full lineage on disk.

Layout:

    archive/
      YYYY/
        MM/
          DD/
            HH-MM-SS-<id>/
              raw.webm                browser-uploaded audio
              audio.wav               transcoded mono 16 kHz, fed to whisper
              transcript.txt          whisper output
              polished.txt            LLM-hub output (only if polished)
              polish_request.json     prompt payload (only if polished)
              polish_response.json    raw hub response (only if polished)
              meta.json               recorder/server params, durations, errors

The whole `archive/` folder is gitignored. Sessions older than the
retention window (default 30 days) are deleted on app start, and on
demand from the UI's Clean button.
"""

from __future__ import annotations

# Standard library imports
import json
import logging
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_ARCHIVE_DIR = Path(__file__).resolve().parent.parent / "archive"
META_FILENAME = "meta.json"


def parse_created_at(value: str) -> Optional[datetime]:
    """Parse a session ``created_at`` string to a naive *local* datetime.

    ``created_at`` is written as ``datetime.now().isoformat(...)`` — naive
    local time. A tz-aware value (e.g. a caller-supplied ``since``) is
    converted to local time and stripped to naive so all comparisons
    happen in one frame. Returns ``None`` if the value can't be parsed.
    """
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt
RAW_AUDIO_FILENAME = "raw.webm"
WAV_AUDIO_FILENAME = "audio.wav"
TRANSCRIPT_FILENAME = "transcript.txt"
POLISHED_FILENAME = "polished.txt"
POLISH_REQUEST_FILENAME = "polish_request.json"
POLISH_RESPONSE_FILENAME = "polish_response.json"


@dataclass
class SessionMeta:
    """Metadata recorded alongside each session."""

    session_id: str
    created_at: str  # ISO 8601 in UTC
    language: Optional[str] = None
    sample_rate: Optional[int] = None
    raw_format: str = "audio/webm;codecs=opus"
    raw_bytes: int = 0
    duration_seconds: Optional[float] = None
    transcript_chars: int = 0
    polish_model: Optional[str] = None
    polish_prompt_id: Optional[str] = None
    polish_succeeded: Optional[bool] = None
    error: Optional[str] = None
    incognito: bool = False  # filtered out of list_sessions when True
    extra: dict = field(default_factory=dict)


@dataclass
class Session:
    """Handle to a single archive folder."""

    session_id: str
    folder: Path
    meta: SessionMeta

    # ---------------------------------------------------------------- writers

    def append_raw_chunk(self, chunk: bytes) -> int:
        """Append a streamed audio chunk; return the new on-disk size.

        Each chunk is appended as soon as it arrives so a connection drop
        mid-recording never loses data.
        """
        path = self.folder / RAW_AUDIO_FILENAME
        with path.open("ab") as fh:
            fh.write(chunk)
        size = path.stat().st_size
        self.meta.raw_bytes = size
        return size

    def write_wav(self, wav_bytes: bytes) -> Path:
        path = self.folder / WAV_AUDIO_FILENAME
        path.write_bytes(wav_bytes)
        return path

    def write_transcript(self, text: str) -> Path:
        path = self.folder / TRANSCRIPT_FILENAME
        path.write_text(text, encoding="utf-8")
        self.meta.transcript_chars = len(text)
        return path

    def write_polished(
        self,
        polished_text: str,
        model: str,
        request_payload: dict,
        response_payload: dict,
        prompt_id: Optional[str] = None,
    ) -> Path:
        (self.folder / POLISHED_FILENAME).write_text(
            polished_text, encoding="utf-8"
        )
        (self.folder / POLISH_REQUEST_FILENAME).write_text(
            json.dumps(request_payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.folder / POLISH_RESPONSE_FILENAME).write_text(
            json.dumps(response_payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        self.meta.polish_model = model
        self.meta.polish_prompt_id = prompt_id
        self.meta.polish_succeeded = True
        return self.folder / POLISHED_FILENAME

    def mark_polish_failed(
        self,
        model: str,
        error: str,
        prompt_id: Optional[str] = None,
    ) -> None:
        self.meta.polish_model = model
        self.meta.polish_prompt_id = prompt_id
        self.meta.polish_succeeded = False
        self.meta.error = error

    def write_meta(self) -> Path:
        path = self.folder / META_FILENAME
        path.write_text(
            json.dumps(asdict(self.meta), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    # ---------------------------------------------------------------- readers

    def read_transcript(self) -> Optional[str]:
        path = self.folder / TRANSCRIPT_FILENAME
        return path.read_text(encoding="utf-8") if path.exists() else None

    def read_polished(self) -> Optional[str]:
        path = self.folder / POLISHED_FILENAME
        return path.read_text(encoding="utf-8") if path.exists() else None

    def raw_path(self) -> Path:
        return self.folder / RAW_AUDIO_FILENAME

    def wav_path(self) -> Path:
        return self.folder / WAV_AUDIO_FILENAME


class SessionArchive:
    """Top-level archive — creates sessions, lists them, prunes old ones."""

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = Path(root) if root is not None else DEFAULT_ARCHIVE_DIR
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------ create / lookup

    def new_session(
        self,
        language: Optional[str] = None,
        sample_rate: Optional[int] = None,
        now: Optional[datetime] = None,
        incognito: bool = False,
    ) -> Session:
        ts = now or datetime.now()
        session_id = ts.strftime("%H-%M-%S-") + uuid.uuid4().hex[:8]
        folder = (
            self.root
            / ts.strftime("%Y")
            / ts.strftime("%m")
            / ts.strftime("%d")
            / session_id
        )
        folder.mkdir(parents=True, exist_ok=True)

        meta = SessionMeta(
            session_id=session_id,
            created_at=ts.isoformat(timespec="seconds"),
            language=language,
            sample_rate=sample_rate,
            incognito=incognito,
        )
        session = Session(session_id=session_id, folder=folder, meta=meta)
        session.write_meta()
        logger.info(f"📁 New session {session_id} → {folder}")
        return session

    def get(self, session_id: str) -> Optional[Session]:
        for folder in self._iter_session_folders():
            if folder.name == session_id:
                return self._hydrate(folder)
        return None

    def list_sessions(
        self,
        limit: Optional[int] = None,
        offset: int = 0,
        since: Optional[datetime] = None,
    ) -> List[Session]:
        """Newest-first listing, optionally paginated and date-windowed.

        Incognito sessions are excluded — they exist on disk while the
        recording flow needs them but never surface in History.

        ``since`` (naive local datetime) keeps only sessions created at or
        after that instant — the date-window retrieval the hub dictionary
        miner uses (voice-transcriber#60). A session whose ``created_at``
        can't be parsed is dropped from a windowed query rather than
        leaking ancient data; with no ``since`` it is kept as before. The
        window is applied before ``offset``/``limit``.
        """
        sessions = sorted(
            (self._hydrate(f) for f in self._iter_session_folders()),
            key=lambda s: s.meta.created_at,
            reverse=True,
        )
        sessions = [s for s in sessions if not s.meta.incognito]
        if since is not None:
            kept: List[Session] = []
            for s in sessions:
                created = parse_created_at(s.meta.created_at)
                if created is not None and created >= since:
                    kept.append(s)
            sessions = kept
        if offset:
            sessions = sessions[offset:]
        if limit is not None:
            sessions = sessions[:limit]
        return sessions

    def count_sessions(self) -> int:
        """Total non-incognito session count — used by 'X of Y' hint."""
        return sum(
            1
            for s in (self._hydrate(f) for f in self._iter_session_folders())
            if not s.meta.incognito
        )

    def delete_session(self, session_id: str) -> bool:
        """Remove one session folder. Returns True iff it existed."""
        for folder in self._iter_session_folders():
            if folder.name == session_id:
                shutil.rmtree(folder, ignore_errors=True)
                self._prune_empty_date_folders()
                return True
        return False

    # ------------------------------------------------------ housekeeping

    def cleanup_older_than(self, days: int) -> int:
        """Delete sessions older than `days`. Returns the count removed."""
        cutoff = time.time() - days * 86400
        removed = 0
        for folder in list(self._iter_session_folders()):
            try:
                mtime = folder.stat().st_mtime
            except OSError:
                continue
            if mtime < cutoff:
                shutil.rmtree(folder, ignore_errors=True)
                removed += 1
        if removed:
            logger.info(f"🧹 Pruned {removed} sessions older than {days} days")
        self._prune_empty_date_folders()
        return removed

    def cleanup_all(self) -> int:
        """Wipe every session. Returns the count removed."""
        removed = 0
        for folder in list(self._iter_session_folders()):
            shutil.rmtree(folder, ignore_errors=True)
            removed += 1
        self._prune_empty_date_folders()
        if removed:
            logger.info(f"🧹 Cleared {removed} sessions")
        return removed

    # ---------------------------------------------------------------- helpers

    def _iter_session_folders(self) -> Iterator[Path]:
        if not self.root.exists():
            return
        for year in self.root.iterdir():
            if not year.is_dir():
                continue
            for month in year.iterdir():
                if not month.is_dir():
                    continue
                for day in month.iterdir():
                    if not day.is_dir():
                        continue
                    for session in day.iterdir():
                        if session.is_dir():
                            yield session

    def _prune_empty_date_folders(self) -> None:
        """Tidy up YYYY/MM/DD folders that no longer contain sessions."""
        if not self.root.exists():
            return
        for year in list(self.root.iterdir()):
            if not year.is_dir():
                continue
            for month in list(year.iterdir()):
                if not month.is_dir():
                    continue
                for day in list(month.iterdir()):
                    if day.is_dir() and not any(day.iterdir()):
                        try:
                            day.rmdir()
                        except OSError:
                            pass
                if month.is_dir() and not any(month.iterdir()):
                    try:
                        month.rmdir()
                    except OSError:
                        pass
            if year.is_dir() and not any(year.iterdir()):
                try:
                    year.rmdir()
                except OSError:
                    pass

    def _hydrate(self, folder: Path) -> Session:
        meta_path = folder / META_FILENAME
        meta = SessionMeta(
            session_id=folder.name,
            created_at=datetime.fromtimestamp(folder.stat().st_mtime).isoformat(
                timespec="seconds"
            ),
        )
        if meta_path.exists():
            try:
                raw = json.loads(meta_path.read_text(encoding="utf-8"))
                meta = SessionMeta(
                    session_id=str(raw.get("session_id", folder.name)),
                    created_at=str(raw.get("created_at", meta.created_at)),
                    language=raw.get("language"),
                    sample_rate=raw.get("sample_rate"),
                    raw_format=str(raw.get("raw_format", "audio/webm;codecs=opus")),
                    raw_bytes=int(raw.get("raw_bytes", 0)),
                    duration_seconds=raw.get("duration_seconds"),
                    transcript_chars=int(raw.get("transcript_chars", 0)),
                    polish_model=raw.get("polish_model"),
                    polish_prompt_id=raw.get("polish_prompt_id"),
                    polish_succeeded=raw.get("polish_succeeded"),
                    error=raw.get("error"),
                    incognito=bool(raw.get("incognito", False)),
                    extra=dict(raw.get("extra") or {}),
                )
            except (OSError, json.JSONDecodeError, TypeError) as exc:
                logger.warning(f"⚠️  Stale meta for {folder.name}: {exc}")

        return Session(session_id=folder.name, folder=folder, meta=meta)
