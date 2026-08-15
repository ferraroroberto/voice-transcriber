"""Logic layer — audio capture, HTTP client, app config, diagnostics.

UI surfaces (`app/gui/`, `app/cli/`, `app/webapp/`) consume this package;
nothing in here imports any UI framework. See `CLAUDE.md` for the
`src/` ↔ `app/` split convention shared across the monorepo.
"""

from .app_config import (
    AppConfig,
    WHISPER_LANGUAGES,
    load_app_config,
    resolve_iso,
)
from .diagnostics import (
    BACKEND_CPU,
    BACKEND_CUDA,
    BACKEND_CUDA_BUILD,
    BACKEND_UNKNOWN,
    PortOwner,
    app_log_handler,
    attach_app_log_handler,
    infer_backend,
    port_owner,
)
from .recorder import AudioRecorder, RecordingError
from .transcription_client import (
    TranscriptionClient,
    TranscriptionError,
    build_transcription_client,
)

__all__ = [
    "AppConfig",
    "AudioRecorder",
    "BACKEND_CPU",
    "BACKEND_CUDA",
    "BACKEND_CUDA_BUILD",
    "BACKEND_UNKNOWN",
    "PortOwner",
    "RecordingError",
    "TranscriptionClient",
    "TranscriptionError",
    "WHISPER_LANGUAGES",
    "build_transcription_client",
    "resolve_iso",
    "app_log_handler",
    "attach_app_log_handler",
    "infer_backend",
    "load_app_config",
    "port_owner",
]
