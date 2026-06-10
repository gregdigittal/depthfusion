"""Tests for the safe admin expression evaluator."""
from __future__ import annotations

import pytest

from depthfusion.utils.expression_eval import ExpressionError, evaluate_admin_expression


def test_evaluate_simple_math():
    assert evaluate_admin_expression("2 + 2") == 4


def test_evaluate_string_len():
    assert evaluate_admin_expression("len('hello')") == 5


def test_evaluate_nested_arithmetic():
    assert evaluate_admin_expression("(3 + 4) * 2 - 1") == 13


def test_evaluate_comparison():
    assert evaluate_admin_expression("10 > 3") is True
    assert evaluate_admin_expression("1 == 2") is False


def test_evaluate_allowed_builtins():
    assert evaluate_admin_expression("max(1, 5, 3)") == 5
    assert evaluate_admin_expression("sum([1, 2, 3])") == 6
    assert evaluate_admin_expression("abs(-7)") == 7


# --- Security regression tests: dangerous constructs must be rejected ---


@pytest.mark.parametrize(
    "payload",
    [
        "__import__('os').system('echo pwned')",
        "open('/etc/passwd').read()",
        "(1).__class__.__bases__",
        "().__class__.__mro__",
        "exec('x=1')",
        "eval('1+1')",
        "lambda: 1",
        "[x for x in range(3)]",
        "globals()",
    ],
)
def test_dangerous_expressions_are_rejected(payload: str):
    with pytest.raises(ExpressionError):
        evaluate_admin_expression(payload)


def test_attribute_access_rejected():
    with pytest.raises(ExpressionError):
        evaluate_admin_expression("'a'.upper()")


def test_unknown_function_rejected():
    with pytest.raises(ExpressionError):
        evaluate_admin_expression("os('ls')")


def test_invalid_syntax_raises_expression_error():
    with pytest.raises(ExpressionError):
        evaluate_admin_expression("2 +")


def test_pow_dos_guard():
    with pytest.raises(ExpressionError):
        evaluate_admin_expression("9 ** 99999")
