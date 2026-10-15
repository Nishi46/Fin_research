from datetime import timedelta

from contracts.dataview import DataView
from tests.contract_suite.conftest import ProviderFactory
from tests.helpers import T0, make_bar


def test_bars_are_never_after_end(make_provider: ProviderFactory) -> None:
    provider = make_provider([make_bar("AAA", d) for d in range(10)])
    end = T0 + timedelta(days=4)
    bars = provider.bars(["AAA"], end)
    assert bars
    assert all(b.ts <= end for b in bars)


def test_only_requested_symbols_are_returned(make_provider: ProviderFactory) -> None:
    provider = make_provider([make_bar("AAA", 0), make_bar("BBB", 0)])
    assert {b.symbol for b in provider.bars(["AAA"], T0)} == {"AAA"}


def test_snapshot_id_is_content_addressed(make_provider: ProviderFactory) -> None:
    same_a = make_provider([make_bar("AAA", 0), make_bar("AAA", 1)])
    same_b = make_provider([make_bar("AAA", 0), make_bar("AAA", 1)])
    different = make_provider([make_bar("AAA", 0), make_bar("AAA", 1, close=101.0)])
    assert same_a.snapshot().id == same_b.snapshot().id
    assert same_a.snapshot().id != different.snapshot().id


def test_snapshot_discloses_survivorship_bias_for_free_data(
    make_provider: ProviderFactory,
) -> None:
    assert make_provider([make_bar("AAA", 0)]).snapshot().survivorship_biased is True


def test_membership_is_a_frozenset(make_provider: ProviderFactory) -> None:
    provider = make_provider([make_bar("AAA", 0)])
    assert isinstance(provider.membership("SP500", T0.date()), frozenset)


def test_corporate_actions_are_never_after_end(make_provider: ProviderFactory) -> None:
    provider = make_provider([make_bar("AAA", 0)])
    assert all(a.effective_at <= T0 for a in provider.corporate_actions(["AAA"], T0))


def test_dataview_over_any_provider_blocks_the_future(make_provider: ProviderFactory) -> None:
    provider = make_provider([make_bar("AAA", d) for d in range(10)])
    view = DataView(provider, asof=T0 + timedelta(days=3))
    assert max(b.ts for b in view.bars(["AAA"])) == T0 + timedelta(days=3)
