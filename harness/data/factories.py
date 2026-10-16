from collections.abc import Callable, Sequence

from contracts.data import Bar, DataProvider
from harness.data.provider import ComposedDataProvider

PROVIDER_FACTORIES: dict[str, Callable[[Sequence[Bar]], DataProvider]] = {
    "composed": lambda bars: ComposedDataProvider(bars),
}
