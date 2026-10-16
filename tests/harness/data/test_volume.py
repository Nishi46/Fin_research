import pytest

from harness.data.volume import VolumeBasis, resolve_volume
from tests.data_fixtures import normal_series


def test_consolidated_volume_used_when_available() -> None:
    iex = normal_series(n=5)
    full = [b.model_copy(update={"volume": b.volume * 20}) for b in iex]
    resolved = resolve_volume(iex, full)
    assert resolved.basis is VolumeBasis.CONSOLIDATED
    assert [b.volume for b in resolved.bars] == [b.volume for b in full]
    assert [b.close for b in resolved.bars] == [b.close for b in iex]


def test_iex_only_is_kept_unscaled_by_default_and_labelled() -> None:
    iex = normal_series(n=5)
    resolved = resolve_volume(iex, None)
    assert resolved.basis is VolumeBasis.IEX_ONLY
    assert [b.volume for b in resolved.bars] == [b.volume for b in iex]


def test_scale_above_one_is_refused_because_it_would_overstate_liquidity() -> None:
    with pytest.raises(ValueError, match="overstate"):
        resolve_volume(normal_series(n=3), None, iex_scale=2.0)


def test_scale_below_one_shrinks_volume() -> None:
    iex = normal_series(n=3)
    resolved = resolve_volume(iex, None, iex_scale=0.5)
    assert resolved.bars[0].volume == pytest.approx(iex[0].volume * 0.5)


def test_partial_consolidated_falls_back_per_bar() -> None:
    iex = normal_series(n=4)
    full = [iex[0].model_copy(update={"volume": 5e6})]
    resolved = resolve_volume(iex, full)
    assert resolved.bars[0].volume == 5e6
    assert resolved.bars[1].volume == iex[1].volume
    assert resolved.basis is VolumeBasis.MIXED
