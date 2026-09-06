"""Tests for the polling thread.

These caught a real bug: naming an attribute `_stop` shadowed
threading.Thread._stop(), which made is_alive() raise TypeError once the thread
had finished — so the app could never notice a dead poller and recover.
"""

from __future__ import annotations

import queue
import threading
from datetime import datetime, timezone

import pytest

from little_green_dot import app as appmod
from little_green_dot.app import Poller
from little_green_dot.aws import Check, State
from little_green_dot.config import Config


@pytest.fixture
def stub_check(monkeypatch):
    """Replace the AWS call with something instant and controllable."""
    state = {"state": State.VALID, "calls": 0}

    def fake(config):
        state["calls"] += 1
        return Check(state["state"], datetime.now(timezone.utc))

    monkeypatch.setattr(appmod.aws, "check_credentials", fake)
    return state


def drain_one(results: queue.Queue, timeout: float = 5.0) -> Check:
    return results.get(timeout=timeout)


def test_poller_does_not_shadow_thread_methods():
    """Regression guard: an instance attribute must never hide a Thread method.

    Only methods matter here — Thread.__init__ legitimately sets some private
    instance attributes of its own, such as _initialized.
    """
    methods = {
        name for name, value in vars(threading.Thread).items()
        if name.startswith("_") and callable(value)
    }
    poller = Poller(Config(), queue.Queue())

    clashes = {name for name in vars(poller) if name in methods}

    assert not clashes, f"Poller attributes shadow Thread methods: {clashes}"
    assert "_stop" in methods, "sanity check: _stop really is a Thread method"


def test_is_alive_still_works_after_the_thread_finishes(stub_check):
    """This raised TypeError before the _stop rename."""
    results: queue.Queue[Check] = queue.Queue()
    poller = Poller(Config(interval_seconds=5), results)

    poller.start()
    drain_one(results)
    poller.stop()
    poller.join(timeout=5)

    assert poller.is_alive() is False  # must not raise


def test_stop_ends_the_thread_promptly(stub_check):
    results: queue.Queue[Check] = queue.Queue()
    poller = Poller(Config(interval_seconds=3600), results)  # would sleep an hour

    poller.start()
    drain_one(results)
    poller.stop()
    poller.join(timeout=5)

    assert not poller.is_alive(), "stop() must interrupt the wait, not wait it out"


def test_check_now_interrupts_the_wait(stub_check):
    results: queue.Queue[Check] = queue.Queue()
    poller = Poller(Config(interval_seconds=3600), results)

    poller.start()
    drain_one(results)
    poller.check_now()
    second = drain_one(results, timeout=5)  # arrives immediately, not in an hour

    assert second is not None
    poller.stop()
    poller.join(timeout=5)


def test_an_exploding_check_does_not_kill_the_poller(monkeypatch):
    """The dot must keep trying even if a check blows up unexpectedly."""
    calls = {"n": 0}

    def boom(config):
        calls["n"] += 1
        raise RuntimeError("something unforeseen")

    monkeypatch.setattr(appmod.aws, "check_credentials", boom)
    results: queue.Queue[Check] = queue.Queue()
    poller = Poller(Config(interval_seconds=5, invalid_interval_seconds=5), results)

    poller.start()
    first = drain_one(results)

    assert first.state is State.UNKNOWN
    assert poller.is_alive()
    poller.stop()
    poller.join(timeout=5)
