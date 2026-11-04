"""Background-refreshed cache for slow reachability probes.

A probe (HTTP health check, PATH lookup) can take seconds when its target is
busy or down. Run on a request, it makes the poll that asked for it wait; run
on the event loop, it stalls every other request too. This cache answers a
read from memory and refreshes the probe on a worker thread:

* a value younger than ``fresh_s`` is served as is;
* an older value is served together with its age while a refresh runs, until
  it passes ``max_age_s``, after which it is never served as current;
* a probe with no usable value is waited on for at most ``wait_s``, then
  reported as unknown (``None``), never as up or down.

Nothing polls an upstream while nothing reads the cache: the read is what
starts a refresh.
"""

from __future__ import annotations

# Standard library imports
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)


def _spawn_thread(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="probe-cache", daemon=True).start()


@dataclass
class _Slot:
    probe: Callable[[], Any]
    value: Any = None
    at: Optional[float] = None
    inflight: bool = False


class ProbeCache:
    def __init__(
        self,
        probes: Mapping[str, Callable[[], Any]],
        *,
        fresh_s: float = 10.0,
        max_age_s: float = 60.0,
        wait_s: float = 0.2,
        clock: Callable[[], float] = time.monotonic,
        spawn: Callable[[Callable[[], None]], None] = _spawn_thread,
    ) -> None:
        self._slots: Dict[str, _Slot] = {n: _Slot(p) for n, p in probes.items()}
        self._fresh_s = fresh_s
        self._max_age_s = max_age_s
        self._wait_s = wait_s
        self._clock = clock
        self._spawn = spawn
        self._cond = threading.Condition()

    def prime(self) -> None:
        """Start every probe now so the first read finds an answer."""
        with self._cond:
            for slot in self._slots.values():
                self._kick(slot)

    def read(self, name: str) -> Tuple[Any, Optional[float]]:
        """``(value, age_seconds)``; ``(None, None)`` while the probe is unknown."""
        slot = self._slots[name]
        with self._cond:
            age = self._usable_age(slot)
            if age is not None:
                if age > self._fresh_s:
                    self._kick(slot)
                return slot.value, age
            self._kick(slot)
            self._cond.wait_for(lambda: self._usable_age(slot) is not None, timeout=self._wait_s)
            age = self._usable_age(slot)
            return (slot.value, age) if age is not None else (None, None)

    def _usable_age(self, slot: _Slot) -> Optional[float]:
        if slot.at is None:
            return None
        age = self._clock() - slot.at
        return age if age <= self._max_age_s else None

    def _kick(self, slot: _Slot) -> None:
        # Single flight: a slow probe is never stacked behind itself.
        if slot.inflight:
            return
        slot.inflight = True
        self._spawn(lambda: self._run(slot))

    def _run(self, slot: _Slot) -> None:
        try:
            value = slot.probe()
        except Exception as exc:  # noqa: BLE001 — a failed probe is "unknown", never "up"
            logger.warning(f"⚠️  probe failed, leaving it unknown: {exc}")
            value, ok = None, False
        else:
            ok = True
        with self._cond:
            if ok:
                slot.value, slot.at = value, self._clock()
            slot.inflight = False
            self._cond.notify_all()
