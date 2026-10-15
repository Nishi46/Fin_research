import os
from collections.abc import Mapping


def os_env() -> Mapping[str, str]:
    """The only place the engine reads the process environment."""
    return os.environ
