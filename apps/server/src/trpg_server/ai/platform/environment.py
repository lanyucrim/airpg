from __future__ import annotations

import os
from pathlib import Path
import re


_ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")


def load_backend_environment(path: Path | None = None) -> None:
    """Load ignored local backend settings without overriding process env."""
    environment_path = path or Path(__file__).resolve().parents[4] / ".env"
    if not environment_path.is_file():
        return
    for raw_line in environment_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or _ENV_KEY.fullmatch(key) is None:
            raise ValueError(f"invalid backend environment entry: {key or line}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


__all__ = ["load_backend_environment"]
