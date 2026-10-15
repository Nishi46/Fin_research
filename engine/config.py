from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, ValidationError

from contracts.capital import CapitalConfig

PAPER_HOST = "paper-api.alpaca.markets"
LIVE_HOSTS = frozenset({"api.alpaca.markets"})
DEFAULT_BROKER_URL = f"https://{PAPER_HOST}"


class ConfigError(Exception):
    pass


class EngineConfig(BaseModel):
    """All engine settings, read from an environment mapping and nothing else.

    Takes the mapping as an argument so it behaves the same on the owner's machine and on a
    stateless CI runner, and so tests need no real environment.
    """

    model_config = ConfigDict(frozen=True)

    broker_base_url: str
    registry_url: str
    capital: CapitalConfig
    host: str

    @classmethod
    def from_env(cls, env: Mapping[str, str], *, now: datetime) -> "EngineConfig":
        registry_url = env.get("LAB_REGISTRY_URL")
        if not registry_url:
            raise ConfigError("LAB_REGISTRY_URL is required")

        broker_url = cls._broker_url(
            env.get("LAB_BROKER_BASE_URL", DEFAULT_BROKER_URL), env.get("LAB_ALLOW_LIVE") == "1"
        )

        return cls(
            broker_base_url=broker_url,
            registry_url=registry_url,
            capital=cls._capital(env, now),
            host=env.get("LAB_HOST", "local"),
        )

    @staticmethod
    def _broker_url(url: str, allow_live: bool) -> str:
        """Accept only exact, https, userinfo-free, port-free hosts; live needs the opt-in.

        Credentials are sent to this host, so anything ambiguous is refused rather than
        parsed leniently (userinfo tricks, backslashes, ports, look-alike suffixes).
        """
        try:
            parsed = urlparse(url)
            host = parsed.hostname
            port = parsed.port
        except ValueError as exc:
            raise ConfigError(f"invalid LAB_BROKER_BASE_URL: {url!r}") from exc
        exact = (
            host is not None
            and parsed.scheme == "https"
            and port is None
            and url == url.strip()
            and "\\" not in url
            and parsed.netloc.lower() == host
            and parsed.path in ("", "/")
            and not parsed.params
            and not parsed.query
            and not parsed.fragment
            and url.isprintable()
        )
        if not exact or host is None:
            raise ConfigError(f"invalid LAB_BROKER_BASE_URL: {url!r}")
        normalized = f"https://{host}"
        if host == PAPER_HOST:
            return normalized
        if host in LIVE_HOSTS:
            if not allow_live:
                raise ConfigError("live broker endpoint requires LAB_ALLOW_LIVE=1 (owner opt-in)")
            return normalized
        raise ConfigError(f"unknown broker host: {host}")

    @staticmethod
    def _capital(env: Mapping[str, str], now: datetime) -> CapitalConfig:
        raw = env.get("LAB_CAPITAL_USD")
        try:
            if raw is None:
                return CapitalConfig(effective_at=now, source="default")
            return CapitalConfig(amount=Decimal(raw), effective_at=now, source="env")
        except (InvalidOperation, ValidationError) as exc:
            raise ConfigError(f"invalid LAB_CAPITAL_USD: {raw!r}") from exc
