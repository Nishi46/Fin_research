"""Data-layer configuration from environment variables only. Never reads files for secrets."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DATA_URL = "https://data.alpaca.markets"
# Consolidated tape: the free tier serves SIP daily bars from 2016-01-04 (queries must end more than
# 15 minutes ago). IEX-only has gaps, history from about 2020-07 and roughly 1/60 of the volume.
DEFAULT_FEED = "sip"
DEFAULT_CALLS_PER_MINUTE = 200
DEFAULT_CORPORATE_ACTIONS_PATH = "/v1/corporate-actions"


@dataclass(frozen=True)
class DataConfig:
    cache_dir: Path
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)
    data_url: str = DEFAULT_DATA_URL
    feed: str = DEFAULT_FEED
    calls_per_minute: int = DEFAULT_CALLS_PER_MINUTE
    corporate_actions_path: str = DEFAULT_CORPORATE_ACTIONS_PATH

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "DataConfig":
        def need(name: str) -> str:
            value = env.get(name, "")
            if not value:
                raise ValueError(f"missing required environment variable {name}")
            return value

        actions_path = env.get("ALPACA_CORPORATE_ACTIONS_PATH", DEFAULT_CORPORATE_ACTIONS_PATH)
        if not actions_path.startswith("/") or "://" in actions_path:
            raise ValueError("ALPACA_CORPORATE_ACTIONS_PATH must be a path starting with '/'")

        return cls(
            cache_dir=Path(need("LAB_DATA_CACHE_DIR")),
            api_key=need("ALPACA_API_KEY"),
            api_secret=need("ALPACA_API_SECRET"),
            data_url=env.get("ALPACA_DATA_URL", DEFAULT_DATA_URL),
            feed=env.get("ALPACA_DATA_FEED", DEFAULT_FEED),
            calls_per_minute=int(env.get("ALPACA_CALLS_PER_MINUTE", DEFAULT_CALLS_PER_MINUTE)),
            corporate_actions_path=actions_path,
        )
