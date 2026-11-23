"""Named thread pools for the routes' blocking work.

The event loop's default executor is one shared pool: a whisper call that
takes a minute, or a polish request waiting on the LLM hub, would each hold
one of its threads while every other route queues behind them. So each kind
of blocking work gets its own bounded pool, and a stall fills only its own:

* :data:`AUDIO_FILES`: local audio work on a take's files (ffmpeg transcode,
  the silence check, the gain boost), short and CPU- or disk-bound;
* :data:`SERVICE_CALLS`: HTTP calls to other services (whisper transcription,
  the LLM hub's polish, task-os), which can each take seconds to minutes.

The pools are :class:`TimedExecutor`, so a request's ``Server-Timing`` names
the pool its time went to. A pool's threads start on first use.
"""

from __future__ import annotations

# Standard library imports
import asyncio
import contextvars
import functools
from typing import Any, Callable, TypeVar

# Local imports
from src.request_timing import TimedExecutor

T = TypeVar("T")

# A final transcode, a partial pass's transcode and a gain boost can overlap
# for one take; a few more cover a second device recording at the same time.
AUDIO_FILES = TimedExecutor("audio-files", max_workers=4)
# A final transcription, a partial pass, a polish and a task-os send can all be
# in flight at once; whisper itself serialises, so more threads would only wait.
SERVICE_CALLS = TimedExecutor("service-calls", max_workers=6)


async def run_on(pool: TimedExecutor, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """``fn(*args, **kwargs)`` on ``pool``, with the caller's context, as ``asyncio.to_thread`` does."""
    loop = asyncio.get_running_loop()
    call = functools.partial(contextvars.copy_context().run, fn, *args, **kwargs)
    return await loop.run_in_executor(pool, call)
