from collections.abc import Iterator, Mapping
from typing import Annotated

from pydantic import AfterValidator, JsonValue, PlainSerializer


class FrozenMap[K, V](Mapping[K, V]):
    """Read-only mapping that survives pickle and deepcopy (unlike MappingProxyType).

    A frozen pydantic model still holds a mutable dict; copying into this view stops a holder
    invalidating an object after validation. Shallow: nested JSON values stay mutable, and
    code determined to reach `_data` can. Instances are unhashable, like the dict they wrap.
    """

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[K, V]) -> None:
        self._data = dict(data)

    def __getitem__(self, key: K) -> V:
        return self._data[key]

    def __iter__(self) -> Iterator[K]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"FrozenMap({self._data!r})"

    def __reduce__(self) -> tuple[type["FrozenMap[K, V]"], tuple[dict[K, V]]]:
        return (type(self), (self._data,))


def _freeze[K, V](value: Mapping[K, V]) -> Mapping[K, V]:
    return FrozenMap(value)


FrozenFloatMap = Annotated[
    Mapping[str, float],
    AfterValidator(_freeze),
    PlainSerializer(dict, return_type=dict[str, float]),
]
FrozenJsonMap = Annotated[
    Mapping[str, JsonValue],
    AfterValidator(_freeze),
    PlainSerializer(dict, return_type=dict[str, JsonValue]),
]
