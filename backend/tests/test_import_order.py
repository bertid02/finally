"""Every package must import cleanly as the *first* import of a process.

`app/llm/` imports `app.api`'s valuation and error helpers; `app/api/chat.py`
imports `app.llm` to run a turn. That is a real cycle between the two packages,
and whether it bites depends purely on which one Python reaches first. The app
(`app/main.py`) and the rest of this suite both reach `app.api` first, so a cycle
here breaks nothing they do -- `import app.llm` in a fresh interpreter failed
outright while all 627 tests passed at 100% coverage, because coverage measures
which lines ran, not the order they were reached in.

Hence a subprocess per module: import order cannot be un-done inside a process
that has already imported either package, so `importlib.reload` would prove
nothing here.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

# Each of these must stand alone. `app.llm` and `app.llm.*` are the ones that
# actually regressed; the rest are cheap and pin the same property.
MODULES = [
    "app.llm",
    "app.llm.client",
    "app.llm.prompt",
    "app.llm.service",
    "app.api",
    "app.api.chat",
    "app.api.valuation",
    "app.main",
]


@pytest.mark.parametrize("module", MODULES)
def test_module_imports_first_in_a_fresh_interpreter(module: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"`import {module}` fails when it is the first import of the process.\n"
        f"This is an import cycle, not a missing dependency -- see the module "
        f"docstring in this file and in app/api/__init__.py.\n\n{result.stderr}"
    )
