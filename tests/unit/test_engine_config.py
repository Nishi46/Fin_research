from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from engine.config import ConfigError, EngineConfig

NOW = datetime(2026, 1, 5, 21, 0, tzinfo=UTC)
BASE = {"LAB_REGISTRY_URL": "sqlite:///registry.sqlite"}


def test_defaults_to_paper_endpoint_and_default_capital() -> None:
    cfg = EngineConfig.from_env(BASE, now=NOW)
    assert "paper" in cfg.broker_base_url
    assert cfg.capital.amount == Decimal("10000")
    assert cfg.capital.source == "default"


def test_capital_from_env_is_marked_as_env() -> None:
    cfg = EngineConfig.from_env({**BASE, "LAB_CAPITAL_USD": "25000"}, now=NOW)
    assert cfg.capital.amount == Decimal("25000")
    assert cfg.capital.source == "env"


@pytest.mark.parametrize("bad", ["abc", "0", "-5", "NaN"])
def test_bad_capital_is_a_config_error(bad: str) -> None:
    with pytest.raises(ConfigError):
        EngineConfig.from_env({**BASE, "LAB_CAPITAL_USD": bad}, now=NOW)


def test_registry_url_is_required() -> None:
    with pytest.raises(ConfigError, match="LAB_REGISTRY_URL"):
        EngineConfig.from_env({}, now=NOW)


def test_live_endpoint_needs_explicit_opt_in() -> None:
    live = {**BASE, "LAB_BROKER_BASE_URL": "https://api.alpaca.markets"}
    with pytest.raises(ConfigError, match="live"):
        EngineConfig.from_env(live, now=NOW)
    cfg = EngineConfig.from_env({**live, "LAB_ALLOW_LIVE": "1"}, now=NOW)
    assert cfg.broker_base_url == "https://api.alpaca.markets"


def test_engine_source_has_no_host_assumptions() -> None:
    banned = ("expanduser", "Path.home", "getcwd", "/Users/", "/home/", "os.environ")
    for source in Path(__file__).resolve().parents[2].joinpath("engine").glob("*.py"):
        text = source.read_text()
        if source.name == "env.py":
            continue
        for needle in banned:
            assert needle not in text, f"{source.name} contains {needle}"


@pytest.mark.parametrize(
    "url",
    [
        "http://paper-api.alpaca.markets",
        "https://paper-api.alpaca.markets:99999",
        "https://paper-api.alpaca.markets:8443",
        "https://user@paper-api.alpaca.markets",
        "https://evil.com\\@paper-api.alpaca.markets",
        "https://paper-api.alpaca.markets.evil.com",
        "https://paper-api.alpaca.markets@evil.com",
        "https://[::1",
        "not a url",
        "https://paper-api.alpaca.markets/\n",
        "https://paper-api.alpaca.markets/?x=1",
        "https://paper-api.alpaca.markets/#frag",
        " https://paper-api.alpaca.markets",
    ],
)
def test_spoofed_or_malformed_endpoints_are_config_errors(url: str) -> None:
    with pytest.raises(ConfigError):
        EngineConfig.from_env({**BASE, "LAB_BROKER_BASE_URL": url}, now=NOW)
    with pytest.raises(ConfigError):
        EngineConfig.from_env({**BASE, "LAB_BROKER_BASE_URL": url, "LAB_ALLOW_LIVE": "1"}, now=NOW)


@pytest.mark.parametrize("url", ["https://evil.com", "http://api.alpaca.markets"])
def test_live_opt_in_only_unlocks_the_known_live_host(url: str) -> None:
    with pytest.raises(ConfigError):
        EngineConfig.from_env({**BASE, "LAB_BROKER_BASE_URL": url, "LAB_ALLOW_LIVE": "1"}, now=NOW)


def test_host_name_case_is_not_a_bypass_or_a_failure() -> None:
    env = {**BASE, "LAB_BROKER_BASE_URL": "https://PAPER-API.alpaca.markets"}
    cfg = EngineConfig.from_env(env, now=NOW)
    assert "paper" in cfg.broker_base_url.lower()


def test_accepted_urls_are_normalized_to_scheme_and_host() -> None:
    env = {**BASE, "LAB_BROKER_BASE_URL": "https://PAPER-API.alpaca.markets/"}
    assert EngineConfig.from_env(env, now=NOW).broker_base_url == "https://paper-api.alpaca.markets"
