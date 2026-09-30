"""Clocks the replay runs on: the real one, and a fake one for tests."""

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def sleep_until(self, deadline: float) -> None: ...

    def wall(self) -> datetime: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep_until(self, deadline: float) -> None:
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)

    def wall(self) -> datetime:
        return datetime.now(UTC)


@dataclass
class FakeClock:
    """Advances only when told to sleep, and records every sleep."""

    start_wall: datetime = datetime(2024, 3, 15, 8, 0, tzinfo=UTC)
    now: float = 0.0
    sleeps: list[float] = field(default_factory=list)

    def monotonic(self) -> float:
        return self.now

    def sleep_until(self, deadline: float) -> None:
        if deadline > self.now:
            self.sleeps.append(deadline - self.now)
            self.now = deadline

    def wall(self) -> datetime:
        return self.start_wall + timedelta(seconds=self.now)
