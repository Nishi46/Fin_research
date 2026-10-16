from collections.abc import Sequence

from contracts.dataview import DataView


def closes_by_symbol(data: DataView, symbols: Sequence[str]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {s: [] for s in symbols}
    for bar in sorted(data.bars(symbols), key=lambda b: b.ts):
        out[bar.symbol].append(bar.close)
    return out
