"""HTTP client for the local whisper.cpp server.

Calls the OpenAI-compatible `/v1/audio/transcriptions` endpoint that
whisper.cpp's `whisper-server` exposes.

Two endpoints are supported, selected per-request via the ``translate`` flag:

- ``base_url``           — primary turbo server (transcription, fast, GPU).
- ``translate_base_url`` — secondary translate-capable server (e.g. the
  local-llm-hub's :8091 proxy loaded with ggml-medium.bin). CPU-only with
  a 3-8 s cold-start the first time after idle.

Translate requests carry ``task=translate`` as a multipart form field; the
server returns the translated English text in the same OpenAI shape.
"""

from __future__ import annotations

# Standard library imports
import io
import logging
import platform
import re
import wave
from pathlib import Path
from typing import Optional, Union

# Third-party imports
import numpy as np
import requests

from .app_config import AppConfig, resolve_iso
from .snippets import apply_snippets
from .speaker_label import strip_speaker_label
from .vocabulary import prompt_for_language

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 300  # long audio on slow GPUs


class TranscriptionError(Exception):
    """Raised when the server returns an error or is unreachable."""


class _Unreachable(Exception):
    """Internal: a *transport* failure (connection refused / timeout) — the
    endpoint could not be reached at all, as opposed to an HTTP error response.

    Only a transport failure against the primary transcribe endpoint makes the
    client fall back to the local whisper-server; a real HTTP error (non-200)
    is surfaced as-is, never silently substituted.
    """

    def __init__(self, url: str, cause: Exception) -> None:
        super().__init__(str(cause))
        self.url = url
        self.cause = cause


HEADER_SERVED_MODEL = "X-Hub-Served-Model"
HEADER_SERVED_HOST = "X-Hub-Served-Host"

# Sentinel served-model/host pair used whenever a transcription did not go
# through the hub's observability proxy — a fallback to the local
# whisper-server, or the translate proxy (which never sets the headers, see
# whisper_translate_proxy.py in local-llm-hub). Distinguishes "we genuinely
# don't know" from "the hub told us" for UI consumers (issue #156).
_LOCAL_FALLBACK_MODEL = "whisper (local fallback)"


class TranscriptionClient:
    """Tiny wrapper around whisper-server's OpenAI-shaped audio endpoints."""

    def __init__(
        self,
        base_url: str,
        timeout: float = DEFAULT_TIMEOUT,
        translate_base_url: Optional[str] = None,
        fallback_base_url: Optional[str] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        # Translation falls back to the primary URL when no separate translate
        # server is configured — turbo will accept ``task=translate`` but the
        # output will be junk, which is on the user to debug. The two-server
        # wiring is the supported path.
        self.translate_base_url = (translate_base_url or base_url).rstrip("/")
        # Direct whisper-server URL to retry against when the primary transcribe
        # endpoint (typically the hub :8000) is *unreachable*. ``None`` disables
        # the fallback — the pre-hub behaviour. See ``build_transcription_client``.
        self.fallback_base_url = fallback_base_url.rstrip("/") if fallback_base_url else None
        self.timeout = timeout
        self._session = requests.Session()
        # Which backend/host actually served the most recent transcription
        # (issue #156) — read from the hub's X-Hub-Served-* response headers
        # (local-llm-hub#412). Best-effort, display-only: this app is a
        # single-desktop-user process, so no locking against a concurrent
        # request — a rare race just shows a stale label for one tick.
        self.last_served_model: str = ""
        self.last_served_host: str = ""

    def close(self) -> None:
        self._session.close()

    # ------------------------------------------------------------- public API

    def transcribe_wav_bytes(
        self,
        wav_bytes: bytes,
        language: Optional[str] = None,
        filename: str = "audio.wav",
        translate: bool = False,
    ) -> str:
        iso = resolve_iso(language) if language else None
        data = {"response_format": "json"}
        # Only send `language` when transcribing — the :8091 translate proxy
        # (as of 2026-05-09) treats `language` as a hard "transcribe in this
        # language" hint that masks `task=translate`. Whisper auto-detects
        # source language reliably for translation anyway, so dropping the
        # hint costs nothing and unblocks the toggle. Remove this branch
        # once the sibling proxy honours `task=translate` regardless of
        # `language`.
        if iso and not translate:
            data["language"] = iso
        if translate:
            data["task"] = "translate"
        prompt = prompt_for_language(iso)
        if prompt:
            data["prompt"] = prompt

        files = {"file": (filename, wav_bytes, "audio/wav")}

        # Translate has a single endpoint (no hub role); transcribe is hub-first
        # with a local-whisper fallback when the hub itself is unreachable.
        if translate:
            try:
                return self._transcribe_once(self.translate_base_url, data, files)
            except _Unreachable as u:
                raise TranscriptionError(f"could not reach {u.url}: {u.cause}") from u.cause

        try:
            return self._transcribe_once(self.base_url, data, files)
        except _Unreachable as u:
            if not self.fallback_base_url or self.fallback_base_url == self.base_url:
                raise TranscriptionError(f"could not reach {u.url}: {u.cause}") from u.cause
            logger.warning(
                f"⚠️ transcribe endpoint {self.base_url} unreachable ({u.cause}) — "
                f"falling back to local whisper {self.fallback_base_url}"
            )
            try:
                return self._transcribe_once(self.fallback_base_url, data, files)
            except _Unreachable as u2:
                raise TranscriptionError(
                    f"could not reach fallback {u2.url}: {u2.cause}"
                ) from u2.cause

    def _transcribe_once(self, base: str, data: dict, files: dict) -> str:
        """POST one audio request to ``base`` and return the extracted text.

        Raises ``_Unreachable`` on a transport failure (so the caller can decide
        whether to fall back) and ``TranscriptionError`` on a non-200 response
        (a real server error — never a fallback trigger).
        """
        url = base + "/v1/audio/transcriptions"
        prompt = data.get("prompt")
        logger.info(
            f"📤 POST {url} (language={data.get('language', 'auto')}"
            f"{', task=translate' if data.get('task') == 'translate' else ''}"
            f"{', vocab=' + str(prompt.count(',') + 1) + ' terms' if prompt else ''})"
        )
        try:
            response = self._session.post(url, data=data, files=files, timeout=self.timeout)
        except requests.RequestException as e:
            raise _Unreachable(url, e) from e

        if response.status_code != 200:
            raise TranscriptionError(
                f"server returned {response.status_code}: {response.text[:500]}"
            )

        self._record_served_from(response)
        return apply_snippets(strip_speaker_label(_extract_text(response)))

    def _record_served_from(self, response: requests.Response) -> None:
        """Stash who actually served this transcription (issue #156).

        The hub stamps ``X-Hub-Served-Model``/``X-Hub-Served-Host`` on every
        ``/v1/audio/*`` response (local-llm-hub#412). A bare whisper-server —
        the local fallback path, or the translate proxy, neither of which
        goes through the hub's observability proxy — never sets them, so
        that case falls back to a local sentinel rather than showing a
        previous take's (now stale) served pair.
        """
        served_model = response.headers.get(HEADER_SERVED_MODEL, "").strip()
        served_host = response.headers.get(HEADER_SERVED_HOST, "").strip()
        if served_model:
            self.last_served_model = served_model
            self.last_served_host = served_host or _local_machine_name()
        else:
            self.last_served_model = _LOCAL_FALLBACK_MODEL
            self.last_served_host = _local_machine_name()

    def transcribe_array(
        self,
        samples: np.ndarray,
        sample_rate: int,
        language: Optional[str] = None,
        translate: bool = False,
    ) -> str:
        wav_bytes = samples_to_wav_bytes(samples, sample_rate)
        return self.transcribe_wav_bytes(
            wav_bytes, language=language, translate=translate,
        )

    def transcribe_file(
        self,
        path: Union[str, Path],
        language: Optional[str] = None,
        translate: bool = False,
    ) -> str:
        path = Path(path)
        if not path.exists():
            raise TranscriptionError(f"audio file not found: {path}")
        return self.transcribe_wav_bytes(
            path.read_bytes(),
            language=language,
            filename=path.name,
            translate=translate,
        )


# ------------------------------------------------------------------- factory


def build_transcription_client(
    config: AppConfig,
    whisper_base_url: str,
    timeout: float = DEFAULT_TIMEOUT,
) -> TranscriptionClient:
    """Construct a hub-first ``TranscriptionClient`` with a local-whisper fallback.

    The single wiring point every call site (tray, webapp, GUI, CLI) shares so
    the routing can't drift between them:

    - transcription → ``config.transcribe_base_url`` (the hub :8000 → parakeet +
      the hub's role-failover chain, with observability);
    - on a *transport* failure against the hub (its process is down), retry the
      local whisper-server at ``whisper_base_url`` (the :8090 this app owns);
    - translation → ``config.translate_base_url`` (its own proven endpoint).
    """
    return TranscriptionClient(
        config.transcribe_base_url,
        timeout=timeout,
        translate_base_url=config.translate_base_url,
        fallback_base_url=whisper_base_url,
    )


# --------------------------------------------------------------------- helpers


def _local_machine_name() -> str:
    try:
        return platform.node() or "this machine"
    except Exception:
        return "this machine"


def samples_to_wav_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    """Encode an int16 mono numpy array as a WAV blob in memory."""
    if samples.dtype != np.int16:
        samples = samples.astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())
    return buf.getvalue()


def _extract_text(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return _flatten(response.text)
    if isinstance(payload, dict) and "text" in payload:
        return _flatten(str(payload["text"]))
    return _flatten(response.text)


_WS_RUN = re.compile(r"\s+")


def _flatten(text: str) -> str:
    """Collapse any run of whitespace (newlines, tabs, multiple spaces) into
    a single space. whisper-server returns one segment per line; clipboard
    consumers want a clean single-line stream.
    """
    return _WS_RUN.sub(" ", text).strip()
