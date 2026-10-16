"""Data-layer configuration from environment variables only. Never reads files for secrets."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DATA_URL = "https://data.alpaca.markets"
DEFAULT_FEED = "iex"
DEFAULT_CALLS_PER_MINUTE = 200


@dataclass(frozen=True)
class DataConfig:
    cache_dir: Path
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)
    data_url: str = DEFAULT_DATA_URL
    feed: str = DEFAULT_FEED
    calls_per_minute: int = DEFAULT_CALLS_PER_MINUTE

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "DataConfig":
        def need(name: str) -> str:
            value = env.get(name, "")
            if not value:
                raise ValueError(f"missing required environment variable {name}")
            return value

        return cls(
            cache_dir=Path(need("LAB_DATA_CACHE_DIR")),
            api_key=need("ALPACA_API_KEY"),
            api_secret=need("ALPACA_API_SECRET"),
            data_url=env.get("ALPACA_DATA_URL", DEFAULT_DATA_URL),
            feed=env.get("ALPACA_DATA_FEED", DEFAULT_FEED),
            calls_per_minute=int(env.get("ALPACA_CALLS_PER_MINUTE", DEFAULT_CALLS_PER_MINUTE)),
        )
