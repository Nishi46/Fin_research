from harness.registry import factories


def test_each_factory_returns_a_fresh_instance() -> None:
    for table in (
        factories.REGISTRY_FACTORIES,
        factories.RUNLOG_FACTORIES,
        factories.INBOX_FACTORIES,
    ):
        assert table
        for make in table.values():
            assert make() is not make()
