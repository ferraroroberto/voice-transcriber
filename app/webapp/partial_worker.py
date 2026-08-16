"""Rolling transcription worker — pillar 1 of the latency-collapse plan.

While a session is recording, this worker runs whisper on the
*accumulated* audio every ``partial_interval_seconds``, so by the time
the user taps Stop the transcript box is already filled. The final
``/finish`` pass is short-circuited when the last partial already
covers the full take.

The worker is a per-session object stored on ``app.state.partial_workers``
and lives until the SSE stream closes or the session ends.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# Skip a partial pass until the take has accumulated at least this many
# bytes of raw audio — running whisper against a near-empty file just
# burns CPU for a partial that would be discarded anyway.
_MIN_RAW_BYTES_FOR_PASS = 4096


@dataclass
class _Event:
    """One SSE message destined for every subscriber."""

    kind: str  # "partial" | "final"
    payload: Dict[str, Any]


@dataclass
class PartialWorker:
    """Per-session background worker.

    ``transcribe`` runs whisper.cpp against a WAV path and returns text.
    ``transcode`` shells out to ffmpeg (raw.webm → WAV). Both are async
    callables — wrap sync code with ``asyncio.to_thread`` at the call
    site so this module stays transport-agnostic.
    """

    session: Any  # archive.Session — typed as Any to avoid circular import
    interval_seconds: float
    transcribe: Callable[[Path], Awaitable[str]]
    transcode: Callable[[Path, Path], Awaitable[None]]
    # Reap this worker (self-stop the run loop) after this many seconds with
    # no `mark_dirty()` call — a take abandoned mid-recording (phone screen
    # locks, tab closed, tunnel drops) otherwise leaves the task parked on
    # `_dirty.wait()` forever (voice-transcriber#178). ``0`` disables reaping.
    stale_after_seconds: float = 0.0
    # Called once, from inside `_run`, when this worker reaps itself for
    # staleness — the owner uses it to drop the dict entry it can no longer
    # reach directly (this dataclass has no back-reference to the registry).
    on_stale: Optional[Callable[[], None]] = None

    # Mutable state (filled by start()/run()).
    _dirty: asyncio.Event = field(default_factory=asyncio.Event)
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _subscribers: List[asyncio.Queue] = field(default_factory=list)
    _task: Optional[asyncio.Task] = None
    partial_version: int = 0
    partial_text: str = ""
    last_bytes_at_partial: int = 0
    finalised: bool = False
    _reaped: bool = False

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())

    def mark_dirty(self) -> None:
        """Signal that new audio bytes have landed since the last pass."""
        self._dirty.set()

    async def stop(self) -> None:
        """Stop the worker — used on /finish or on session deletion."""
        self._stop.set()
        self._dirty.set()  # wake the loop out of its wait
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"⚠️  partial worker stop raised: {exc}")
        # Close all open SSE subscriber queues so any GET /events
        # generator exits cleanly.
        for q in self._subscribers:
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass

    # ------------------------------------------------------------ subscribe

    async def subscribe(self) -> AsyncIterator[_Event]:
        """Yield events as they happen. Backfills the most recent partial
        so a late-connecting client sees the current state immediately.
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._subscribers.append(q)
        try:
            if self.partial_text:
                yield _Event(
                    kind="partial",
                    payload={
                        "version": self.partial_version,
                        "transcript": self.partial_text,
                    },
                )
            if self.finalised:
                yield _Event(kind="final", payload={"transcript": self.partial_text})
                return
            while True:
                evt = await q.get()
                if evt is None:
                    return
                yield evt
        finally:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    # ------------------------------------------------------------ broadcast

    async def _broadcast(self, event: _Event) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("⚠️  SSE subscriber queue full — dropping event")

    # ------------------------------------------------------------ main loop

    async def _run(self) -> None:
        """Wait for dirty signal → debounce → run a pass → broadcast.

        One pass at a time; no overlapping whisper calls per session. A
        session that goes quiet for ``stale_after_seconds`` (no chunk, so
        no `mark_dirty()`) is treated as abandoned and reaps itself rather
        than parking this task forever.
        """
        try:
            while not self._stop.is_set():
                if self.stale_after_seconds > 0:
                    try:
                        await asyncio.wait_for(
                            self._dirty.wait(), timeout=self.stale_after_seconds,
                        )
                    except asyncio.TimeoutError:
                        if self._stop.is_set():
                            break
                        logger.info(
                            f"🧹 partial worker for {self.session.session_id} idle "
                            f"> {self.stale_after_seconds:.0f}s — reaping abandoned session"
                        )
                        self._reaped = True
                        break
                else:
                    await self._dirty.wait()
                self._dirty.clear()
                if self._stop.is_set():
                    break
                # Debounce so a flurry of chunks coalesces into one pass.
                await asyncio.sleep(self.interval_seconds)
                if self._stop.is_set():
                    break
                try:
                    await self._run_pass()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"⚠️  partial pass errored: {exc}")
                    # Light backoff on repeated errors so we don't spin.
                    await asyncio.sleep(self.interval_seconds)
        except asyncio.CancelledError:
            pass
        finally:
            if self._reaped and self.on_stale is not None:
                try:
                    self.on_stale()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"⚠️  partial worker on_stale callback failed: {exc}")
            logger.debug(f"🛑 partial worker exited for {self.session.session_id}")

    async def _run_pass(self) -> None:
        raw_path: Path = self.session.raw_path()
        if not raw_path.exists():
            return
        size = raw_path.stat().st_size
        if size < _MIN_RAW_BYTES_FOR_PASS:
            return
        if size == self.last_bytes_at_partial:
            return

        # Snapshot the current raw bytes to a sibling file so the
        # actively-appending raw.webm isn't read mid-write by ffmpeg.
        snap_raw = self.session.folder / "partial_raw.webm"
        try:
            shutil.copyfile(raw_path, snap_raw)
        except OSError as exc:
            logger.warning(f"⚠️  could not snapshot raw audio: {exc}")
            return

        snap_wav = self.session.folder / "partial.wav"
        try:
            await self.transcode(snap_raw, snap_wav)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"partial transcode skipped: {exc}")
            return

        t0 = time.monotonic()
        try:
            text = await self.transcribe(snap_wav)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"partial transcribe failed: {exc}")
            return
        elapsed = time.monotonic() - t0

        text = (text or "").strip()
        if not text:
            # Whisper returned nothing — could just be the first 1-2 s of
            # warm-up. Don't broadcast empty partials.
            self.last_bytes_at_partial = size
            return

        self.partial_version += 1
        self.partial_text = text
        self.last_bytes_at_partial = size

        try:
            (self.session.folder / "transcript_partial.txt").write_text(
                text, encoding="utf-8"
            )
        except OSError as exc:
            logger.debug(f"persist partial failed: {exc}")

        logger.info(
            f"📝 partial v{self.partial_version} for {self.session.session_id}: "
            f"{len(text)} chars in {elapsed:.2f}s"
        )

        await self._broadcast(_Event(
            kind="partial",
            payload={"version": self.partial_version, "transcript": text},
        ))

    # ------------------------------------------------------------ finalise

    async def finalise(self, final_transcript: str) -> None:
        """Called from /finish — broadcast a 'final' event and stop."""
        self.partial_text = final_transcript
        self.finalised = True
        await self._broadcast(_Event(
            kind="final",
            payload={"transcript": final_transcript},
        ))


# ----------------------------------------------------------------- SSE plumbing


def encode_sse(event_kind: str, payload: Dict[str, Any]) -> str:
    """Format one SSE message in the standard wire shape."""
    body = json.dumps(payload, ensure_ascii=False)
    return f"event: {event_kind}\ndata: {body}\n\n"
