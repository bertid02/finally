"""Fixtures for database tests.

Every test gets its own file-backed database under `tmp_path`. A file rather than
`:memory:` on purpose: the file path exercises the WAL pragma and the parent
directory creation that production actually runs, and a test suite that only ever
touched an in-memory database would not notice either breaking. The `:memory:`
branch has its own dedicated tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app.db import Database, Repository


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "nested" / "finally.db"


@pytest.fixture
async def db(db_path: Path) -> AsyncIterator[Database]:
    database = Database(db_path)
    yield database
    database.close()


@pytest.fixture
async def repo(db: Database) -> Repository:
    """An initialized repository: schema created, defaults seeded."""
    repository = Repository(db)
    await repository.initialize()
    return repository


@pytest.fixture
async def raw_repo(db: Database) -> Repository:
    """An uninitialized repository, for tests that drive initialization themselves."""
    return Repository(db)
