"""HTTP client for the local whisper.cpp server.

Calls the OpenAI-compatible `/v1/audio/transcriptions` endpoint that
whisper.cpp's `whisper-server` exposes. Translation is requested via the
`translate=true` form field on the same endpoint (whisper-server does
not expose a separate `/v1/audio/translations` route).
"""

from __future__ import annotations

# Standard library imports
import io
import logging
import re
import wave
from pathlib import Path
from typing import Optional, Union

# Third-party imports
import numpy as np
import requests

logger = logging.getLogger(__name__)

ISO_LANGUAGE_CODES = {
    "Spanish": "es",
    "English": "en",
    "Italian": "it",
    "French": "fr",
    "German": "de",
    "Portuguese": "pt",
    "auto": None,
}

DEFAULT_TIMEOUT = 300  # long audio on slow GPUs


class TranscriptionError(Exception):
    """Raised when the server returns an error or is unreachable."""


class TranscriptionClient:
    """Tiny wrapper around whisper-server's OpenAI-shaped audio endpoints."""

    def __init__(self, base_url: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ------------------------------------------------------------- public API

    def transcribe_wav_bytes(
        self,
        wav_bytes: bytes,
        language: Optional[str] = None,
        translate: bool = False,
        filename: str = "audio.wav",
    ) -> str:
        url = self.base_url + "/v1/audio/transcriptions"

        iso = ISO_LANGUAGE_CODES.get(language, language) if language else None
        data = {"response_format": "json"}
        if iso:
            data["language"] = iso
        if translate:
            data["translate"] = "true"

        files = {"file": (filename, wav_bytes, "audio/wav")}

        logger.info(f"📤 POST {url} (translate={translate}, language={iso or 'auto'})")
        try:
            response = requests.post(url, data=data, files=files, timeout=self.timeout)
        except requests.RequestException as e:
            raise TranscriptionError(f"could not reach {url}: {e}") from e

        if response.status_code != 200:
            raise TranscriptionError(
                f"server returned {response.status_code}: {response.text[:500]}"
            )

        return _extract_text(response)

    def transcribe_array(
        self,
        samples: np.ndarray,
        sample_rate: int,
        language: Optional[str] = None,
        translate: bool = False,
    ) -> str:
        wav_bytes = samples_to_wav_bytes(samples, sample_rate)
        return self.transcribe_wav_bytes(wav_bytes, language=language, translate=translate)

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
            translate=translate,
            filename=path.name,
        )


# --------------------------------------------------------------------- helpers


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
