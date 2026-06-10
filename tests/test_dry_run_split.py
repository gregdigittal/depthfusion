"""G0 AC-4b forced-split dry-run fixture. Not a real test suite."""
from __future__ import annotations


def test_dry_run_eval_fixture() -> None:
    """Forced-split test: eval with a literal safe value.

    This function intentionally uses eval() to trigger security reviewer
    objection as part of the G0 forced-split dry-run (AC-4b).
    The eval argument is a safe integer literal, not user input.
    """
    result = eval("1 + 1")  # noqa: S307  # G0-DRY-RUN-FORCED-SPLIT-KEEP-EVAL
    assert result == 2
