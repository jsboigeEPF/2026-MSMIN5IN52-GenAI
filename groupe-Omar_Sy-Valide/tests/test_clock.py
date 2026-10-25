from datetime import UTC, datetime, timedelta

import pytest

from src.clock import Clock, FakeClock, SystemClock


def test_system_clock_returns_utc_aware_datetime() -> None:
    now = SystemClock().now()
    assert now.utcoffset() == timedelta(0)


def test_fake_clock_is_frozen_until_advanced() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    clock = FakeClock(start)
    assert clock.now() == clock.now() == start
    clock.advance(90)
    assert clock.now() == start + timedelta(seconds=90)
    clock.advance(timedelta(hours=1))
    assert clock.now() == start + timedelta(hours=1, seconds=90)


def test_fake_clock_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError):
        FakeClock(datetime(2026, 1, 1))


def test_fake_clock_never_goes_backwards() -> None:
    with pytest.raises(ValueError):
        FakeClock().advance(-1)


def test_both_clocks_satisfy_protocol() -> None:
    assert isinstance(SystemClock(), Clock)
    assert isinstance(FakeClock(), Clock)
