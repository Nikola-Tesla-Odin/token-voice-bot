"""persona-agent: IConfucius, generalized to any personality."""

import os
from pathlib import Path

__version__ = "0.1.0"


def load_env_file() -> None:
    """Read KEY=VALUE lines from ~/.persona-agent/.env. Real environment variables win.

    Runs at import time so settings like PERSONA_AGENT_MODEL are in place before modules read them.
    """
    home = Path(os.environ.get("PERSONA_AGENT_HOME", Path.home() / ".persona-agent"))
    path = home / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


load_env_file()
