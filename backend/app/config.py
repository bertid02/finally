"""Process configuration, read once from the environment.

Everything the app needs to know about its surroundings lands here so routes and
the lifespan never call `os.getenv` inline -- a test that wants a different
static directory or database path overrides one object rather than patching the
environment in five places.

The database path is deliberately *not* duplicated here: `app.db.Database`
already resolves `FINALLY_DB_PATH` itself (db-engineer's contract), and having a
second reader of the same variable is how the two drift apart. `Settings.db_path`
exists only so `/api/health` can report which file is open.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Where the Dockerfile drops the Next.js static export (frontend-engineer's
# contract in TEAM_LOG.md). Absent in local development, which must not crash.
DEFAULT_STATIC_DIR = "/app/static"


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable snapshot of the environment at app-construction time."""

    static_dir: Path
    db_path: str
    massive_api_key: str
    llm_mock: bool

    @property
    def has_static(self) -> bool:
        """True when a built frontend is present. False is normal for `uv run`."""
        return self.static_dir.is_dir() and (self.static_dir / "index.html").is_file()

    @property
    def requested_source(self) -> str:
        """What the environment *asked* for, before any start() failure.

        `/api/health` compares this against the source's own `name`, so a demo
        that silently fell back to the simulator is visibly doing so.
        """
        return "massive" if self.massive_api_key else "simulator"


def load_settings() -> Settings:
    """Read the environment. Called once per app, not per request."""
    return Settings(
        static_dir=Path(os.getenv("FINALLY_STATIC_DIR", DEFAULT_STATIC_DIR)),
        db_path=os.getenv("FINALLY_DB_PATH", "db/finally.db"),
        massive_api_key=os.getenv("MASSIVE_API_KEY", "").strip(),
        llm_mock=_env_flag("LLM_MOCK"),
    )
