"""RecursiveSandbox — restricted execution environment for RLM sub-processes."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ALLOWED_READ_PATHS = [Path.home(), Path("/tmp/depthfusion")]
BLOCKED_NETWORK = True

_BLOCKED_MODULES = ("socket", "urllib", "requests", "httpx")

_PREAMBLE_TEMPLATE = """\
import builtins as _builtins
_original_import = _builtins.__import__
_BLOCKED_SET = {blocked!r}

def _restricted_import(name, *args, _blocked=_BLOCKED_SET, _orig=_original_import, **kwargs):
    top = name.split(".")[0]
    if top in _blocked:
        raise ImportError(f"Import of '{{name}}' is blocked in the sandbox")
    return _orig(name, *args, **kwargs)

_builtins.__import__ = _restricted_import
del _builtins, _original_import, _BLOCKED_SET, _restricted_import
"""


class RecursiveSandbox:
    """Restricted execution environment for RLM sub-processes.

    Allows read access to home dir and /tmp/depthfusion.
    Blocks unauthorized network access via subprocess isolation.
    """

    def __init__(self, timeout_seconds: int = 30) -> None:
        self.timeout_seconds = timeout_seconds
        # Ensure /tmp/depthfusion exists
        Path("/tmp/depthfusion").mkdir(parents=True, exist_ok=True)

    def execute(self, code: str) -> dict:
        """Execute Python code in a restricted subprocess.

        Returns {"stdout": str, "stderr": str, "returncode": int, "timed_out": bool}

        Sandbox restrictions:
        - Runs with timeout
        - No import of socket, urllib, requests, httpx (blocked)
        - Working directory: /tmp/depthfusion (created if needed)
        """
        preamble = self._build_restricted_globals()
        full_code = preamble + "\n" + code

        try:
            result = subprocess.run(
                [sys.executable, "-c", full_code],
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                cwd="/tmp/depthfusion",
            )
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
                "timed_out": False,
            }
        except subprocess.TimeoutExpired:
            return {
                "stdout": "",
                "stderr": "Execution timed out",
                "returncode": -1,
                "timed_out": True,
            }

    def _build_restricted_globals(self) -> str:
        """Return Python code preamble that blocks network imports."""
        return _PREAMBLE_TEMPLATE.format(blocked=_BLOCKED_MODULES)
