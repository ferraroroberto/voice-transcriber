"""Webapp config + reachability status.

``GET/POST /api/config`` read and patch ``webapp_config.json``;
``GET /api/status`` probes whisper, the LLM hub, and ffmpeg.
"""

from __future__ import annotations

# Standard library imports
from typing import Any, Dict

# Third-party imports
from fastapi import APIRouter, HTTPException, Request

from src.app_config import resolve_iso
from src.polish import PolishClient
from src.polish_prompts import load_polish_prompts
from src.webapp_config import WebappConfig, update_webapp_config
from src.whisper_server import WhisperServerManager

from app.webapp.audio import find_ffmpeg
from app.webapp.routers._helpers import PROJECT_ROOT

router = APIRouter()


@router.get("/api/config")
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
        "gain_boost_enabled": cfg.gain_boost_enabled,
        "gain_boost_db": cfg.gain_boost_db,
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


@router.post("/api/config")
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
        "gain_boost_enabled",
        "gain_boost_db",
    }
    patch = {k: v for k, v in body.items() if k in allowed}
    try:
        new_cfg = update_webapp_config(**patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    request.app.state.webapp_config = new_cfg
    return {"ok": True, "config": _config_dict(new_cfg)}


@router.get("/api/status")
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


def _config_dict(cfg: WebappConfig) -> Dict[str, Any]:
    return {
        "polish_model_default": cfg.polish_model_default,
        "polish_models_available": cfg.polish_models_available,
        "polish_prompt_default": cfg.polish_prompt_default,
        "history_retention_days": cfg.history_retention_days,
        "force_builtin_mic_default": cfg.force_builtin_mic_default,
        "preferred_mic_id": cfg.preferred_mic_id,
    }
