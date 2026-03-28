"""Tests for RecursiveSandbox."""
from __future__ import annotations

from depthfusion.recursive.sandbox import RecursiveSandbox


def test_execute_simple_python():
    sb = RecursiveSandbox(timeout_seconds=10)
    result = sb.execute("x = 1 + 1")
    assert result["returncode"] == 0
    assert result["timed_out"] is False


def test_execute_captures_stdout():
    sb = RecursiveSandbox(timeout_seconds=10)
    result = sb.execute("print('hello sandbox')")
    assert result["returncode"] == 0
    assert "hello sandbox" in result["stdout"]
    assert result["timed_out"] is False


def test_execute_times_out():
    sb = RecursiveSandbox(timeout_seconds=1)
    result = sb.execute("import time; time.sleep(10)")
    assert result["timed_out"] is True
    assert result["returncode"] == -1


def test_execute_blocks_socket_import():
    sb = RecursiveSandbox(timeout_seconds=10)
    result = sb.execute("import socket")
    assert result["returncode"] != 0
    assert "ImportError" in result["stderr"] or "blocked" in result["stderr"].lower()


def test_execute_blocks_urllib_import():
    sb = RecursiveSandbox(timeout_seconds=10)
    result = sb.execute("import urllib")
    assert result["returncode"] != 0


def test_execute_blocks_requests_import():
    sb = RecursiveSandbox(timeout_seconds=10)
    result = sb.execute("import requests")
    assert result["returncode"] != 0


def test_execute_returns_nonzero_on_syntax_error():
    sb = RecursiveSandbox(timeout_seconds=10)
    result = sb.execute("def broken(: pass")
    assert result["returncode"] != 0
    assert result["timed_out"] is False


def test_execute_captures_stderr():
    sb = RecursiveSandbox(timeout_seconds=10)
    result = sb.execute("import sys; sys.stderr.write('err\\n')")
    assert result["returncode"] == 0
    assert "err" in result["stderr"]


def test_result_has_all_keys():
    sb = RecursiveSandbox(timeout_seconds=10)
    result = sb.execute("pass")
    assert set(result.keys()) == {"stdout", "stderr", "returncode", "timed_out"}
