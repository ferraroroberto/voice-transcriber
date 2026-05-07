"""Portable whisper-server management.

This subpackage owns the local whisper.cpp server lifecycle: starting,
stopping, health-checking, and describing the model that's serving. The
sibling `whisper_server.yaml` is the single source of truth for paths,
port, CLI args, and the `mode` (local vs external).
"""

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
