from harness.data.ratelimit import RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_calls_under_limit_do_not_sleep() -> None:
    clock = FakeClock()
    limiter = RateLimiter(max_calls=3, period_s=60.0, clock=clock.time, sleep=clock.sleep)
    for _ in range(3):
        limiter.acquire()
    assert clock.slept == []


def test_call_over_limit_waits_for_window_to_free() -> None:
    clock = FakeClock()
    limiter = RateLimiter(max_calls=2, period_s=60.0, clock=clock.time, sleep=clock.sleep)
    limiter.acquire()
    clock.now = 10.0
    limiter.acquire()
    limiter.acquire()
    assert clock.slept == [50.0]


def test_window_slides() -> None:
    clock = FakeClock()
    limiter = RateLimiter(max_calls=1, period_s=1.0, clock=clock.time, sleep=clock.sleep)
    limiter.acquire()
    clock.now = 5.0
    limiter.acquire()
    assert clock.slept == []
