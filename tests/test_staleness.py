"""Tests for the staleness rule.

The tool's only job is to be trustworthy at a glance, so a result that stopped
refreshing must stop being displayed as if it were current.
"""

from __future__ import annotations

from little_green_dot.app import STALE_GRACE, staleness_limit
from little_green_dot.config import Config


def test_limit_allows_an_ordinary_late_poll():
    config = Config(interval_seconds=60, timeout_seconds=10)

    # A poll that lands a few seconds late must not trip the watchdog.
    assert staleness_limit(config, healthy=True) > 60
    assert staleness_limit(config, healthy=True) == 60 + STALE_GRACE


def test_limit_follows_the_faster_unhealthy_interval():
    config = Config(interval_seconds=600, invalid_interval_seconds=15)

    assert staleness_limit(config, healthy=False) < staleness_limit(config, healthy=True)


def test_limit_leaves_room_for_a_slow_check():
    """A generous timeout must not make every check look stale."""
    config = Config(interval_seconds=30, timeout_seconds=60)

    assert staleness_limit(config, healthy=True) >= 30 + 2 * 60


def test_limit_is_finite_for_every_sane_config():
    for interval in (5, 60, 3600):
        for timeout in (1, 10, 120):
            limit = staleness_limit(
                Config(interval_seconds=interval, timeout_seconds=timeout), healthy=True
            )
            assert interval < limit < interval + 300
