from __future__ import annotations

import os
from pathlib import Path


def load_env(path: str | Path = ".env") -> None:
    """Minimal .env loader: KEY=value lines; real environment variables win."""
    for candidate in (Path(path), Path(__file__).resolve().parents[2] / ".env"):
        if candidate.is_file():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
            return


def data_dir() -> Path:
    return Path(os.environ.get("TALUS_DATA_DIR", "./data")).expanduser()
