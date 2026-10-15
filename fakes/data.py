import hashlib
import random
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta

from contracts.data import Bar, CorporateAction, Snapshot


def synthetic_bars(symbols: Sequence[str], *, start: datetime, days: int, seed: int) -> list[Bar]:
    """Seeded random-walk bars for tests and demos. Not market data."""
    rng = random.Random(seed)
    bars: list[Bar] = []
    for symbol in symbols:
        previous = 100.0
        for day in range(days):
            close = previous * (1.0 + rng.gauss(0.0, 0.01))
            bars.append(
                Bar(
                    symbol=symbol,
                    ts=start + timedelta(days=day),
                    open=previous,
                    high=max(previous, close) * 1.001,
                    low=min(previous, close) * 0.999,
                    close=close,
                    volume=1_000_000.0,
                )
            )
            previous = close
    return bars


class InMemoryDataProvider:
    def __init__(
        self,
        bars: Sequence[Bar],
        corporate_actions: Sequence[CorporateAction] = (),
        memberships: Mapping[str, frozenset[str]] | None = None,
        survivorship_biased: bool = True,
    ) -> None:
        self._bars = sorted(bars, key=lambda b: (b.ts, b.symbol))
        self._actions = list(corporate_actions)
        self._memberships = dict(memberships or {})
        self._survivorship_biased = survivorship_biased

    def snapshot(self) -> Snapshot:
        if not self._bars:
            raise ValueError("cannot snapshot an empty provider")
        digest = hashlib.sha256()
        for bar in self._bars:
            digest.update(bar.model_dump_json().encode())
        return Snapshot(
            id=f"sha256:{digest.hexdigest()[:16]}",
            survivorship_biased=self._survivorship_biased,
            start=self._bars[0].ts,
            end=self._bars[-1].ts,
        )

    def bars(self, symbols: Sequence[str], end: datetime) -> Sequence[Bar]:
        wanted = set(symbols)
        return [b for b in self._bars if b.symbol in wanted and b.ts <= end]

    def corporate_actions(self, symbols: Sequence[str], end: datetime) -> Sequence[CorporateAction]:
        wanted = set(symbols)
        return [a for a in self._actions if a.symbol in wanted and a.effective_at <= end]

    def membership(self, index: str, on: date) -> frozenset[str]:
        return self._memberships.get(index, frozenset())
