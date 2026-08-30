"""`load_settings()` and the `.env` file.

The bug these guard against is not "`.env` is ignored" -- it is that `.env` was
*half* read. LiteLLM's import calls `load_dotenv()` as a side effect, so
`OPENROUTER_API_KEY` arrived from the file by accident, while every variable read
at app construction did not. Setting `LLM_MOCK=true` in `.env` and watching it be
ignored is a long afternoon.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import load_settings


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """No inherited values, and no accidental read of the developer's real .env."""
    for name in ("LLM_MOCK", "MASSIVE_API_KEY", "FINALLY_DB_PATH", "FINALLY_STATIC_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("app.config.find_dotenv", lambda *a, **kw: "")


def _write_env(tmp_path: Path, body: str) -> str:
    path = tmp_path / ".env"
    path.write_text(body)
    return str(path)


def test_values_come_from_the_env_file(monkeypatch, tmp_path):
    """The three that previously did not arrive: all read at app construction."""
    path = _write_env(
        tmp_path,
        "LLM_MOCK=true\nMASSIVE_API_KEY=from-file\nFINALLY_DB_PATH=/from/file.db\n",
    )
    monkeypatch.setattr("app.config.find_dotenv", lambda *a, **kw: path)

    settings = load_settings()
    assert settings.llm_mock is True
    assert settings.massive_api_key == "from-file"
    assert settings.db_path == "/from/file.db"
    assert settings.requested_source == "massive"


def test_an_exported_variable_beats_the_env_file(monkeypatch, tmp_path):
    """`override=False`. The container's --env-file and `docker run -e` must keep
    winning exactly as they did before `.env` was read at all."""
    path = _write_env(tmp_path, "LLM_MOCK=true\nMASSIVE_API_KEY=from-file\n")
    monkeypatch.setattr("app.config.find_dotenv", lambda *a, **kw: path)
    monkeypatch.setenv("MASSIVE_API_KEY", "from-process")
    monkeypatch.setenv("LLM_MOCK", "false")

    settings = load_settings()
    assert settings.massive_api_key == "from-process"
    assert settings.llm_mock is False


def test_no_env_file_is_a_clean_no_op():
    """This checkout has only `.env.example`, and a container has neither."""
    settings = load_settings()
    assert settings.massive_api_key == ""
    assert settings.llm_mock is False
    assert settings.requested_source == "simulator"


def test_a_missing_env_file_path_is_a_clean_no_op(monkeypatch, tmp_path):
    monkeypatch.setattr("app.config.find_dotenv", lambda *a, **kw: str(tmp_path / "absent"))
    assert load_settings().massive_api_key == ""


def test_the_env_file_is_searched_for_from_the_project_root(monkeypatch, tmp_path):
    """`find_dotenv()` is called with no arguments, so it walks up from this
    module towards the project root -- where PLAN.md section 5 puts `.env`."""
    calls = []
    monkeypatch.setattr("app.config.find_dotenv", lambda *a, **kw: calls.append((a, kw)) or "")
    load_settings()
    assert calls == [((), {})]


@pytest.mark.parametrize(
    "raw,expected",
    [("true", True), ("True", True), ("1", True), ("yes", True), ("on", True),
     ("false", False), ("0", False), ("", False), ("nonsense", False)],
)
def test_flag_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("LLM_MOCK", raw)
    assert load_settings().llm_mock is expected
