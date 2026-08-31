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
    """No inherited values, and no accidental read of the developer's real .env.

    `OPENROUTER_API_KEY` belongs in this list as much as the rest, and for a
    reason `find_dotenv` stubbing alone does not cover: an earlier
    `load_dotenv()` -- ours, or the one LiteLLM fires at import -- has already
    copied the developer's real `.env` into `os.environ`, where it outlives any
    stub. Without the delenv, `TestLlmConfigured` asserts False against a real
    key and fails on any machine that has one, which is every machine where chat
    works.
    """
    for name in (
        "LLM_MOCK",
        "MASSIVE_API_KEY",
        "FINALLY_DB_PATH",
        "FINALLY_STATIC_DIR",
        "OPENROUTER_API_KEY",
    ):
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


class TestLlmConfigured:
    """`Settings.llm_configured` is key *presence*, and nothing more about the key."""

    def test_true_when_a_key_is_set(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-secret")
        assert load_settings().llm_configured is True

    def test_false_when_absent(self):
        assert load_settings().llm_configured is False

    @pytest.mark.parametrize("raw", ["", "   ", "\t\n"])
    def test_false_when_blank_or_whitespace(self, monkeypatch, raw):
        monkeypatch.setenv("OPENROUTER_API_KEY", raw)
        assert load_settings().llm_configured is False

    def test_mock_mode_is_not_folded_in(self, monkeypatch):
        """An E2E run under LLM_MOCK=true must not report a key that does not exist.
        `llm_mock` is reported beside it, so a reader can still tell chat works."""
        monkeypatch.setenv("LLM_MOCK", "true")
        settings = load_settings()
        assert settings.llm_mock is True
        assert settings.llm_configured is False

    def test_the_key_comes_from_the_env_file_too(self, monkeypatch, tmp_path):
        path = _write_env(tmp_path, "OPENROUTER_API_KEY=sk-or-from-file\n")
        monkeypatch.setattr("app.config.find_dotenv", lambda *a, **kw: path)
        assert load_settings().llm_configured is True
