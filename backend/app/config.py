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

from dotenv import find_dotenv, load_dotenv

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
    # Defaulted so a test constructing a Settings by hand does not have to
    # enumerate every key it does not care about. The defaults are the same
    # "absent" values `load_settings` produces for an empty environment.
    openrouter_api_key: str = ""
    llm_mock: bool = False

    @property
    def has_static(self) -> bool:
        """True when a built frontend is present. False is normal for `uv run`."""
        return self.static_dir.is_dir() and (self.static_dir / "index.html").is_file()

    @property
    def llm_configured(self) -> bool:
        """Is an OpenRouter key present? Key presence only -- nothing else.

        A boolean, and deliberately never anything more. `/api/health` is
        unauthenticated and gets pasted into bug reports and screen shares, so it
        must never carry the key, a prefix or suffix of it, or even its length: a
        length narrows a brute-force search and a prefix identifies the account.

        Mock mode is *not* folded in here. `llm_mock` is reported beside it, so a
        reader can tell "chat will work" (`llm_mock or llm_configured`) apart from
        "a real key is present" -- and an E2E run under LLM_MOCK=true does not
        report a key that does not exist.
        """
        return bool(self.openrouter_api_key)

    @property
    def requested_source(self) -> str:
        """What the environment *asked* for, before any start() failure.

        `/api/health` compares this against the source's own `name`, so a demo
        that silently fell back to the simulator is visibly doing so.
        """
        return "massive" if self.massive_api_key else "simulator"


def load_settings() -> Settings:
    """Read the environment, `.env` included. Called once per app, not per request.

    The `.env` load is not optional politeness -- without it PLAN.md section 5 is
    only half true, in the worst possible way. LiteLLM calls `load_dotenv()` at
    *its* import, which is lazy and happens on the first chat message, so
    `OPENROUTER_API_KEY` arrives from `.env` by accident while `LLM_MOCK`,
    `MASSIVE_API_KEY` and `FINALLY_DB_PATH` -- read here, at app construction,
    long before that import -- silently do not. The half that fails is the half
    that changes how the app behaves.

    `override=False` is load-bearing: a variable already exported in the process
    beats the file, so the container's `--env-file` and `docker run -e` keep
    winning exactly as they do today. `find_dotenv()` returning "" when there is
    no `.env` (this checkout, and every container) is a clean no-op.
    """
    load_dotenv(find_dotenv(), override=False)
    return Settings(
        static_dir=Path(os.getenv("FINALLY_STATIC_DIR", DEFAULT_STATIC_DIR)),
        db_path=os.getenv("FINALLY_DB_PATH", "db/finally.db"),
        massive_api_key=os.getenv("MASSIVE_API_KEY", "").strip(),
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", "").strip(),
        llm_mock=_env_flag("LLM_MOCK"),
    )
