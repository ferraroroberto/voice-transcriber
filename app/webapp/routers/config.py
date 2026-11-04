"""Webapp config + reachability status.

``GET/POST /api/config`` read and patch ``webapp_config.json``;
``GET /api/status`` probes whisper, the LLM hub, and ffmpeg.
"""

from __future__ import annotations

# Standard library imports
from typing import Any, Dict

# Third-party imports
from fastapi import APIRouter, FastAPI, HTTPException, Request

from src.app_config import resolve_iso
from src.polish_prompts import load_polish_prompts
from src.probe_cache import ProbeCache
from src.webapp_config import WebappConfig, config_to_client_dict, update_webapp_config

from app.webapp.audio import find_ffmpeg
from app.webapp.routers._helpers import (
    PROJECT_ROOT, adopt_webapp_config, current_webapp_config, maybe_json,
)

router = APIRouter()


@router.get("/api/config")
async def get_config(request: Request) -> Dict[str, Any]:
    cfg: WebappConfig = current_webapp_config(request.app)
    app_cfg = request.app.state.app_config
    prompts = load_polish_prompts()
    data = config_to_client_dict(cfg)
    data.update(
        {
            "polish_prompts": [
                {
                    "id": p.id,
                    "label": p.label,
                    "description": p.description,
                    "system": p.system,
                }
                for p in prompts
            ],
            # Latency-collapse knob exposed read-only to the client so
            # the JS can decide whether to subscribe to SSE partials.
            "rolling_transcription_enabled": cfg.partial_interval_seconds > 0,
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
    )
    return data


@router.post("/api/config")
async def patch_config(request: Request) -> Dict[str, Any]:
    body = await maybe_json(request)
    allowed = {
        "polish_model_default",
        "polish_prompt_default",
        "force_builtin_mic_default",
        "preferred_mic_id",
        "history_retention_days",
        "transcript_retention_days",
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
    adopt_webapp_config(request.app, new_cfg)
    return {"ok": True, "config": config_to_client_dict(new_cfg)}


def build_status_cache(app: FastAPI) -> ProbeCache:
    """The reachability probes behind ``/api/status``.

    Each probe resolves its client through ``app.state`` when it runs, so a
    swapped client is probed, not the one captured at build time.
    """
    def whisper() -> Dict[str, Any]:
        st = app.state.server_manager.status()
        return {
            "running": st.running,
            "ownership": st.ownership,
            "base_url": st.base_url,
            "detail": st.detail,
        }

    return ProbeCache({
        "whisper": whisper,
        "llm_hub": lambda: app.state.polish_client.is_reachable(),
        "ffmpeg": lambda: find_ffmpeg(PROJECT_ROOT) is not None,
    })


@router.get("/api/status")
def status(request: Request) -> Dict[str, Any]:
    """Whisper, hub and ffmpeg state, served from the probe cache.

    A plain ``def`` so FastAPI runs it on a worker thread: the bounded wait
    on a cold probe must not hold the event loop. A probe with no answer is
    ``null`` (unknown), never ``false``; ``age_seconds`` says how old each
    answer is.
    """
    app = request.app
    cache: ProbeCache = app.state.status_cache
    whisper, whisper_age = cache.read("whisper")
    hub, hub_age = cache.read("llm_hub")
    ffmpeg, ffmpeg_age = cache.read("ffmpeg")
    if whisper is None:
        whisper = {
            "running": None,
            "ownership": None,
            "base_url": app.state.server_manager.config.base_url,
            "detail": "not checked yet",
        }
    return {
        "whisper": whisper,
        "llm_hub": {"reachable": hub, "base_url": app.state.polish_client.base_url},
        "ffmpeg_present": ffmpeg,
        "age_seconds": {
            name: None if age is None else round(age, 1)
            for name, age in (("whisper", whisper_age), ("llm_hub", hub_age), ("ffmpeg", ffmpeg_age))
        },
    }
