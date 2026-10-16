import time
from collections import deque
from collections.abc import Callable


class RateLimiter:
    """Sliding-window limiter: at most `max_calls` acquisitions per `period_s` seconds."""

    def __init__(
        self,
        max_calls: int,
        period_s: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_calls < 1 or period_s <= 0:
            raise ValueError("max_calls must be >= 1 and period_s > 0")
        self._max = max_calls
        self._period = period_s
        self._clock = clock
        self._sleep = sleep
        self._calls: deque[float] = deque()

    def acquire(self) -> None:
        now = self._clock()
        while self._calls and now - self._calls[0] >= self._period:
            self._calls.popleft()
        if len(self._calls) >= self._max:
            wait = self._period - (now - self._calls[0])
            self._sleep(wait)
            now = self._clock()
            while self._calls and now - self._calls[0] >= self._period:
                self._calls.popleft()
        self._calls.append(now)
