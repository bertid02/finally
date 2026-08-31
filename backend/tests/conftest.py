"""Pytest configuration and fixtures.

asyncio_mode = "auto" in pyproject.toml handles async test collection; no
event-loop fixture is needed (and overriding the policy raises a
DeprecationWarning on Python 3.14+).
"""

import pytest


@pytest.fixture(autouse=True)
def _hermetic_environment(monkeypatch):
    """Every test starts from an environment with none of FinAlly's variables set.

    The suite is run on developer machines that have a real `.env` -- and
    `load_dotenv()` (ours at app construction, or the one LiteLLM fires at
    import) copies that file into `os.environ`, where it persists for the rest of
    the session and outlives any `find_dotenv` stub. Without this, an assertion
    about an *absent* variable passes in CI and fails for the one person whose
    chat panel actually works, which is the least useful direction for a test to
    fail in.

    Function-scoped and autouse, so it is torn down between tests; anything a
    test sets for itself still wins, because a root-conftest fixture is set up
    before the test body and before more local fixtures.
    """
    for name in (
        "OPENROUTER_API_KEY",
        "MASSIVE_API_KEY",
        "LLM_MOCK",
        "FINALLY_DB_PATH",
        "FINALLY_STATIC_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
