"""Safe evaluation of admin-submitted expressions for the dashboard.

SECURITY NOTE
=============
This module deliberately does **not** use :func:`eval` / :func:`exec`.

The original ticket asked for ``return eval(expression)`` behind an
"admin-only, trusted users" rationale. That rationale does not make the
construct safe: ``eval`` on a request-borne string is arbitrary remote code
execution (CWE-95). Authorisation ("only admins can reach this") is orthogonal
to input safety — a compromised admin session, a leaked token, CSRF/SSRF into
the endpoint, or an XSS pivot all turn an unguarded ``eval`` into full server
compromise. A security-critical sink must enforce its own safety rather than
rely on an unstated, unenforced precondition at the calling layer.

Instead, expressions are parsed into an AST and walked against an explicit
allowlist of node types and a small allowlist of safe builtins. Anything
outside the allowlist (attribute access, imports, lambdas, comprehensions,
arbitrary names, etc.) raises :class:`ExpressionError`. This supports the
intended dashboard use case (arithmetic, comparisons, simple builtins such as
``len``) without exposing code execution.
"""
from __future__ import annotations

import ast
import operator
from collections.abc import Callable
from typing import Final

__all__ = ["ExpressionError", "evaluate_admin_expression"]


class ExpressionError(ValueError):
    """Raised when an expression is malformed or uses a disallowed construct."""


# Binary operators that are safe to evaluate.
_BIN_OPS: Final[dict[type[ast.operator], Callable[..., object]]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.BitAnd: operator.and_,
    ast.BitOr: operator.or_,
    ast.BitXor: operator.xor,
    ast.LShift: operator.lshift,
    ast.RShift: operator.rshift,
}

_UNARY_OPS: Final[dict[type[ast.unaryop], Callable[..., object]]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
    ast.Not: operator.not_,
    ast.Invert: operator.invert,
}

_CMP_OPS: Final[dict[type[ast.cmpop], Callable[..., object]]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}

# Builtins safe to call: pure, side-effect-free, no filesystem/network/import.
_SAFE_FUNCS: Final[dict[str, Callable[..., object]]] = {
    "abs": abs,
    "len": len,
    "max": max,
    "min": min,
    "round": round,
    "sum": sum,
    "int": int,
    "float": float,
    "str": str,
    "bool": bool,
    "sorted": sorted,
}

# Bare names that resolve to constants.
_SAFE_NAMES: Final[dict[str, object]] = {
    "True": True,
    "False": False,
    "None": None,
}

# Power guard: cap exponent to avoid trivial CPU/memory DoS (e.g. ``9**9**9``).
_MAX_POW_EXPONENT: Final[int] = 1000

# F2: cap raw input length before handing it to the parser (O(n) parse work).
_MAX_EXPRESSION_LENGTH: Final[int] = 1000

# F3: cap AST walk depth so deeply-nested expressions can't exhaust the stack.
_MAX_NESTING_DEPTH: Final[int] = 100

# F5: cap LShift magnitude so ``1 << 1_000_000`` can't allocate a multi-MB integer.
_MAX_SHIFT_BITS: Final[int] = 64

# F6: sequence types that must not appear as Mult operands (prevents list/str replication).
_SEQUENCE_TYPES: Final[tuple[type, ...]] = (str, bytes, list, tuple)


def evaluate_admin_expression(expression: str) -> object:
    """Evaluate a restricted expression from the admin dashboard.

    Used by admin-only endpoints. Authorisation is the caller's responsibility,
    but — unlike a raw ``eval`` — this function is safe even if an
    unauthenticated or malicious input reaches it: only an explicit allowlist of
    AST node types and builtins is permitted.

    Args:
        expression: The expression source (arithmetic, comparisons, and a small
            set of safe builtins such as ``len``).

    Returns:
        The evaluated result.

    Raises:
        ExpressionError: If the expression is syntactically invalid or uses a
            construct outside the allowlist.
    """
    if not isinstance(expression, str):
        raise ExpressionError("expression must be a string")
    # F2: reject oversized input before the parser even looks at it.
    if len(expression) > _MAX_EXPRESSION_LENGTH:
        raise ExpressionError(
            f"expression exceeds maximum length ({_MAX_EXPRESSION_LENGTH} characters)"
        )
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"invalid syntax: {exc.msg}") from exc
    # F3 (safety net): depth counter in _eval_node is the primary guard;
    # catching RecursionError here ensures the ExpressionError contract is
    # never broken by an unanticipated deeply-nested input.
    try:
        return _eval_node(tree.body)
    except RecursionError:
        raise ExpressionError("expression is too deeply nested") from None


def _eval_node(node: ast.AST, _depth: int = 0) -> object:
    # F3: primary depth guard — raises ExpressionError before the Python stack overflows.
    if _depth >= _MAX_NESTING_DEPTH:
        raise ExpressionError(f"expression is too deeply nested (limit {_MAX_NESTING_DEPTH})")

    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.Name):
        if node.id in _SAFE_NAMES:
            return _SAFE_NAMES[node.id]
        raise ExpressionError(f"name {node.id!r} is not allowed")

    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise ExpressionError(f"operator {type(node.op).__name__} is not allowed")
        left = _eval_node(node.left, _depth + 1)
        right = _eval_node(node.right, _depth + 1)
        if isinstance(node.op, ast.Pow):
            _guard_pow(right)
        if isinstance(node.op, ast.LShift):
            _guard_lshift(right)  # F5
        if isinstance(node.op, ast.Mult):
            _guard_mult(left, right)  # F6
        return op(left, right)

    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise ExpressionError(f"operator {type(node.op).__name__} is not allowed")
        return op(_eval_node(node.operand, _depth + 1))

    if isinstance(node, ast.BoolOp):
        values = [_eval_node(v, _depth + 1) for v in node.values]
        if isinstance(node.op, ast.And):
            result: object = True
            for value in values:
                result = value
                if not value:
                    break
            return result
        # ast.Or
        result = False
        for value in values:
            result = value
            if value:
                break
        return result

    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, _depth + 1)
        for op_node, comparator in zip(node.ops, node.comparators):
            cmp = _CMP_OPS.get(type(op_node))
            if cmp is None:
                raise ExpressionError(
                    f"comparison {type(op_node).__name__} is not allowed"
                )
            right = _eval_node(comparator, _depth + 1)
            if not cmp(left, right):
                return False
            left = right
        return True

    if isinstance(node, ast.Call):
        return _eval_call(node, _depth + 1)

    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        elements = [_eval_node(e, _depth + 1) for e in node.elts]
        if isinstance(node, ast.Tuple):
            return tuple(elements)
        if isinstance(node, ast.Set):
            return set(elements)
        return elements

    raise ExpressionError(f"expression element {type(node).__name__} is not allowed")


def _eval_call(node: ast.Call, _depth: int = 0) -> object:
    if not isinstance(node.func, ast.Name):
        raise ExpressionError("only direct calls to allowed builtins are permitted")
    func = _SAFE_FUNCS.get(node.func.id)
    if func is None:
        raise ExpressionError(f"function {node.func.id!r} is not allowed")
    if node.keywords:
        raise ExpressionError("keyword arguments are not allowed")
    args = [_eval_node(a, _depth + 1) for a in node.args]
    return func(*args)


def _guard_pow(exponent: object) -> None:
    if isinstance(exponent, int) and abs(exponent) > _MAX_POW_EXPONENT:
        raise ExpressionError(
            f"exponent exceeds maximum allowed ({_MAX_POW_EXPONENT})"
        )


def _guard_lshift(shift: object) -> None:
    # F5: ``1 << 1_000_000`` allocates a ~125 KB integer; no dashboard use case needs this.
    if isinstance(shift, int) and shift > _MAX_SHIFT_BITS:
        raise ExpressionError(
            f"left-shift amount exceeds maximum allowed ({_MAX_SHIFT_BITS} bits)"
        )


def _guard_mult(left: object, right: object) -> None:
    # F6: ``[0] * 100_000_000`` or ``"a" * 10_000_000`` allocates unbounded memory.
    # Sequence replication has no legitimate use in admin dashboard arithmetic.
    if isinstance(left, _SEQUENCE_TYPES) or isinstance(right, _SEQUENCE_TYPES):
        raise ExpressionError(
            "multiplication of sequence types is not allowed"
        )
