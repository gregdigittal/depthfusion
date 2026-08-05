"""Collection fixups and auth-env isolation for ``tests/test_integration/``.

Two concerns live here: keyword-expression fixups (below) and neutralising
ambient authentication configuration (see ``_AUTH_MODE_ENV_VARS``).

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

import os
import re
from pathlib import Path

import pytest

_HERE = Path(__file__).parent.resolve()

# ---------------------------------------------------------------------------
# Ambient auth-mode isolation
# ---------------------------------------------------------------------------
# ``api/auth.py::_build_principal_dep`` picks one of three auth modes from the
# environment at *import* time: full OIDC, legacy bearer token, or an
# "unconfigured" sentinel that raises 503 on every protected route.  Several
# suites in this directory assert that third mode — they document that V2's
# OIDC replaced per-request bearer auth, so a ``DEPTHFUSION_API_TOKEN`` must
# have no effect and protected routes answer 503.
#
# That precondition is not hermetic.  ``core/config.py::_load_env_file`` runs at
# import time and copies every key from the developer-machine file
# ``~/.claude/depthfusion.env`` into ``os.environ`` (for keys not already set).
# On any machine that actually runs a DepthFusion server that file carries
# ``DEPTHFUSION_V2_LEGACY_AUTH=1`` plus a real ``DEPTHFUSION_API_TOKEN``, so:
#
#   * tests asserting the unconfigured sentinel saw a legacy bearer dep and got
#     401 instead of 503; and
#   * tests that ``delenv("DEPTHFUSION_API_TOKEN")`` left the legacy flag set
#     with no token, so ``_build_principal_dep`` raised ValueError at import and
#     the fixture errored during setup.
#
# Because the injection happens on import of any ``depthfusion`` module, it
# cannot be defeated from the caller's shell (``env -u`` is re-populated), and
# because two modules here import ``depthfusion`` at module scope, ``auth.py``
# can be imported during *collection* — before any fixture runs.  So the vars
# are cleared in ``pytest_configure`` (fixing import-time mode selection) *and*
# per test via the autouse fixture (so a leaked ``setenv`` cannot bleed across
# tests).  Auth mode is thereby decided only by what a test sets explicitly.
_AUTH_MODE_ENV_VARS = (
    "DEPTHFUSION_JWKS_URI",
    "DEPTHFUSION_OIDC_ISSUER",
    "DEPTHFUSION_OIDC_AUDIENCE",
    "DEPTHFUSION_V2_LEGACY_AUTH",
)


@pytest.fixture(autouse=True)
def _neutralise_ambient_auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear inherited auth-mode env vars before every test in this directory.

    Autouse and function-scoped, so it runs before the explicitly-requested
    fixtures that build a ``TestClient``.  It shares the test's own
    ``monkeypatch`` instance, so a test that deliberately sets one of these
    vars afterwards still wins, and everything is restored on teardown.
    """
    for var in _AUTH_MODE_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

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
    # Clear before collection: two modules here import ``depthfusion`` at module
    # scope, so ``auth.py`` (and its module-level dep singleton) can be built
    # during collection, ahead of any fixture.
    for var in _AUTH_MODE_ENV_VARS:
        os.environ.pop(var, None)

    expr = getattr(config.option, "keyword", "") or ""
    if "integration" not in expr or not _all_args_under_here(config):
        return
    config.option.keyword = _INTEGRATION_TERM.sub("", expr)
