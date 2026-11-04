"""ProbeCache: reads never wait on a slow probe, and an unanswered probe is unknown."""

from __future__ import annotations

import threading

from src.probe_cache import ProbeCache


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _inline(work) -> None:
    work()


def _cache(probe, clock, **kw) -> ProbeCache:
    return ProbeCache({"p": probe}, clock=clock, spawn=_inline, **kw)


class _Threads:
    """A spawn that runs each probe on a real thread the test can join."""

    def __init__(self) -> None:
        self.started = []

    def __call__(self, work) -> None:
        t = threading.Thread(target=work, daemon=True)
        self.started.append(t)
        t.start()

    def join(self) -> None:
        for t in self.started:
            t.join(5)


def test_first_read_runs_the_probe_and_reports_zero_age():
    assert _cache(lambda: True, FakeClock()).read("p") == (True, 0.0)


def test_fresh_value_is_served_without_probing_again():
    clock, calls = FakeClock(), []
    cache = _cache(lambda: calls.append(1) or len(calls), clock, fresh_s=10)
    cache.read("p")
    clock.now += 9
    assert cache.read("p") == (1, 9.0)
    assert calls == [1]


def test_stale_value_is_served_with_its_age_while_a_refresh_runs():
    clock, calls, queued = FakeClock(), [], []
    cache = ProbeCache(
        {"p": lambda: calls.append(1) or len(calls)},
        clock=clock, spawn=queued.append, fresh_s=10, max_age_s=60, wait_s=0.01,
    )
    cache.read("p")  # nothing usable yet: kicks the probe, waits, times out
    queued.pop(0)()
    assert cache.read("p") == (1, 0.0)
    clock.now += 30
    # The old answer comes back with its age; the refresh it kicked lands after.
    assert cache.read("p") == (1, 30.0)
    queued.pop(0)()
    assert cache.read("p") == (2, 0.0)


def test_value_older_than_max_age_is_never_served_as_current():
    clock, release = FakeClock(), threading.Event()
    answers = iter([True, "second"])

    def probe():
        value = next(answers)
        if value == "second":
            release.wait(5)
        return value

    spawn = _Threads()
    cache = ProbeCache({"p": probe}, clock=clock, spawn=spawn, max_age_s=60, wait_s=0.02)
    assert cache.read("p") == (True, 0.0)
    clock.now += 61
    try:
        assert cache.read("p") == (None, None)
    finally:
        release.set()
        spawn.join()


def test_a_probe_that_has_not_answered_is_unknown_not_down():
    release, started = threading.Event(), threading.Event()

    def slow():
        started.set()
        release.wait(5)
        return False

    spawn = _Threads()
    cache = ProbeCache({"p": slow}, clock=FakeClock(), spawn=spawn, wait_s=0.02)
    try:
        assert cache.read("p") == (None, None)
        assert started.wait(2)
        # Still waiting on the same probe: no second one is stacked behind it.
        assert cache.read("p") == (None, None)
        assert len(spawn.started) == 1
    finally:
        release.set()
        spawn.join()
    assert cache.read("p") == (False, 0.0)


def test_a_failing_probe_stays_unknown():
    def boom():
        raise RuntimeError("hub exploded")

    assert _cache(boom, FakeClock(), wait_s=0.01).read("p") == (None, None)


def test_a_failing_refresh_keeps_the_last_answer_until_it_ages_out():
    clock, state = FakeClock(), {"fail": False}

    def probe():
        if state["fail"]:
            raise RuntimeError("down")
        return True

    cache = _cache(probe, clock, fresh_s=10, max_age_s=60, wait_s=0.01)
    cache.read("p")
    state["fail"] = True
    clock.now += 20
    assert cache.read("p") == (True, 20.0)
    clock.now += 50
    assert cache.read("p") == (None, None)


def test_prime_probes_before_any_read():
    calls = []
    cache = _cache(lambda: calls.append(1) or True, FakeClock())
    cache.prime()
    assert calls == [1]
    assert cache.read("p") == (True, 0.0)
