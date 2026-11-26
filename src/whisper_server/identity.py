"""What is listening on the shared ASR port.

The port has two possible owners, a hub's speech-recognition backend and this
app's own whisper.cpp, and any other program can take it too. A held port says
nothing about which, so the listener is asked for its identity before it is
adopted. Two answers count as speech recognition:

* whisper.cpp, named by its ``Server`` header on every response (its
  ``/health`` body carries no engine or model);
* a backend whose ``/health`` reports ``status: ok`` with an ``engine`` and a
  ``model``.

No answer is its own state, never folded into "yes" or "no".
"""

from __future__ import annotations

# Standard library imports
from dataclasses import dataclass
from typing import Any, Optional

# Third-party imports
import requests

LISTENER_NONE = "none"                # nothing holds the port
LISTENER_ASR = "asr"                  # a speech-recognition backend
LISTENER_NOT_ASR = "not_asr"          # answered, and is something else
LISTENER_UNCONFIRMED = "unconfirmed"  # held, but no usable answer
ENGINE_WHISPER_CPP = "whisper.cpp"


@dataclass(frozen=True)
class ListenerIdentity:
    kind: str  # LISTENER_* constant
    engine: Optional[str] = None
    model: Optional[str] = None  # None when the backend does not report one
    note: str = ""  # why it was classed this way


def identify_listener(
    session: requests.Session, base_url: str, timeout: float
) -> ListenerIdentity:
    """Classify whatever answers ``GET <base_url>/health``."""
    try:
        response = session.get(base_url + "/health", timeout=timeout)
    except requests.RequestException as exc:
        return ListenerIdentity(
            LISTENER_UNCONFIRMED, note=f"no answer to GET /health: {type(exc).__name__}"
        )
    if ENGINE_WHISPER_CPP in response.headers.get("Server", "").lower():
        return ListenerIdentity(
            LISTENER_ASR,
            engine=ENGINE_WHISPER_CPP,
            note="loading model" if response.status_code == 503 else "",
        )
    if response.status_code == 503:
        return ListenerIdentity(LISTENER_UNCONFIRMED, note="GET /health answered 503")
    if response.status_code != 200:
        return ListenerIdentity(
            LISTENER_NOT_ASR, note=f"GET /health answered {response.status_code}"
        )
    try:
        body: Any = response.json()
    except ValueError:
        return ListenerIdentity(LISTENER_NOT_ASR, note="GET /health is not JSON")
    fields = body if isinstance(body, dict) else {}
    engine, model = fields.get("engine"), fields.get("model")
    if not (isinstance(engine, str) and engine and isinstance(model, str) and model):
        return ListenerIdentity(LISTENER_NOT_ASR, note="GET /health names no engine and model")
    if fields.get("status") != "ok":
        return ListenerIdentity(
            LISTENER_UNCONFIRMED,
            note=f"{engine} reports status {fields.get('status')!r}",
        )
    return ListenerIdentity(LISTENER_ASR, engine=engine, model=model)
