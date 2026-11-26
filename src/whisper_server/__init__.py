"""Portable whisper-server management.

This subpackage owns the local whisper.cpp server lifecycle: starting,
stopping, health-checking, and describing the model that's serving. The
`config/whisper_server.yaml` is the single source of truth for paths,
port, CLI args, and the `mode` (local vs external).
"""

from .identity import (
    LISTENER_ASR,
    LISTENER_NONE,
    LISTENER_NOT_ASR,
    LISTENER_UNCONFIRMED,
)
from .manager import (
    MODE_EXTERNAL,
    MODE_LOCAL,
    OWNERSHIP_EXTERNAL,
    OWNERSHIP_NONE,
    OWNERSHIP_OURS,
    ServerConfig,
    ServerDescription,
    ServerStatus,
    WhisperServerManager,
    load_config,
)

__all__ = [
    "LISTENER_ASR",
    "LISTENER_NONE",
    "LISTENER_NOT_ASR",
    "LISTENER_UNCONFIRMED",
    "MODE_EXTERNAL",
    "MODE_LOCAL",
    "OWNERSHIP_EXTERNAL",
    "OWNERSHIP_NONE",
    "OWNERSHIP_OURS",
    "ServerConfig",
    "ServerDescription",
    "ServerStatus",
    "WhisperServerManager",
    "load_config",
]
