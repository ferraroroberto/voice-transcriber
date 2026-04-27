"""Core transcription modules: audio capture, HTTP client, app config."""

from .app_config import (
    AppConfig,
    LANGUAGE_MODE_LABELS,
    LANGUAGE_MODES,
    load_app_config,
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
from .transcription_client import TranscriptionClient, TranscriptionError

__all__ = [
    "AppConfig",
    "AudioRecorder",
    "BACKEND_CPU",
    "BACKEND_CUDA",
    "BACKEND_CUDA_BUILD",
    "BACKEND_UNKNOWN",
    "LANGUAGE_MODE_LABELS",
    "LANGUAGE_MODES",
    "PortOwner",
    "RecordingError",
    "TranscriptionClient",
    "TranscriptionError",
    "app_log_handler",
    "attach_app_log_handler",
    "infer_backend",
    "load_app_config",
    "port_owner",
]
