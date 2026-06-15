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


# --- V2-DEC-001 DoS regression tests (F2/F3/F5/F6) ---


def test_f2_length_guard():
    """F2: expression longer than _MAX_EXPRESSION_LENGTH is rejected before parsing."""
    with pytest.raises(ExpressionError, match="exceeds maximum length"):
        evaluate_admin_expression("1 + " * 300)


def test_f3_nesting_depth_guard():
    """F3: deeply-nested expression raises ExpressionError, not RecursionError."""
    deeply_nested = "1 + (" * 110 + "1" + ")" * 110
    with pytest.raises(ExpressionError, match="too deeply nested"):
        evaluate_admin_expression(deeply_nested)


def test_f5_lshift_guard():
    """F5: large left-shift is rejected to prevent multi-MB integer allocation."""
    with pytest.raises(ExpressionError, match="left-shift amount exceeds"):
        evaluate_admin_expression("1 << 1000000")


def test_f5_lshift_small_is_allowed():
    """Small shifts within the allowed range still work."""
    assert evaluate_admin_expression("1 << 4") == 16


def test_f6_string_replication_rejected():
    """F6: sequence * int is rejected regardless of operand order."""
    with pytest.raises(ExpressionError, match="sequence types"):
        evaluate_admin_expression("'a' * 1000000")


def test_f6_list_replication_rejected():
    with pytest.raises(ExpressionError, match="sequence types"):
        evaluate_admin_expression("[0] * 100000000")


def test_f6_int_mult_still_works():
    """Pure numeric multiplication must still work."""
    assert evaluate_admin_expression("6 * 7") == 42
