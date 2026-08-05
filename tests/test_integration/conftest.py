"""Collection fixups for ``tests/test_integration/``.

pyproject's ``addopts`` carries ``-k 'not (collector_reliability or integration)'``.
``-k`` matches every node name on an item's path — including this *directory's*
node name, ``test_integration`` — so every test in here is deselected even when
its path is passed explicitly, which is the documented way to run these suites
(``norecursedirs`` already keeps them out of the default run).  The result is
``no tests collected`` / exit code 5 rather than a real result.

When (and only when) every invocation argument lives under this directory, drop
the ``integration`` term from the keyword expression so the requested tests
actually run.  The ``collector_reliability`` term is preserved, and the
``integration`` *marker* exclusion is unaffected (that is expressed with ``-m``,
not ``-k``).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_HERE = Path(__file__).parent.resolve()

# ``not (collector_reliability or integration)`` → ``not (collector_reliability)``
_INTEGRATION_TERM = re.compile(r"\s+or\s+integration\b|\bintegration\s+or\s+")


def _all_args_under_here(config: pytest.Config) -> bool:
    args = [a for a in config.args if not a.startswith("-")]
    if not args:
        return False
    for arg in args:
        target = Path(arg.split("::", 1)[0]).resolve()
        if target != _HERE and _HERE not in target.parents:
            return False
    return True


def pytest_configure(config: pytest.Config) -> None:
    expr = getattr(config.option, "keyword", "") or ""
    if "integration" not in expr or not _all_args_under_here(config):
        return
    config.option.keyword = _INTEGRATION_TERM.sub("", expr)
