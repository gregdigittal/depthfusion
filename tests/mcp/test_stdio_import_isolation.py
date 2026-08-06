"""S-263: stdio server must import cleanly with only core deps (no vps-cpu extras)."""
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


def test_stdio_imports_with_core_deps_only():
    """Create a minimal venv with only core deps and verify stdio import succeeds.

    This test is slow (~30s on cold pip cache) and is excluded from the default
    CI run (norecursedirs covers tests/mcp/). Run manually or via packaging-gate.yml.
    """
    project_root = Path(__file__).parent.parent.parent

    with tempfile.TemporaryDirectory(prefix="df-stdio-iso-") as tmpdir:
        venv_dir = Path(tmpdir) / "venv"
        venv.create(str(venv_dir), with_pip=True, clear=True)
        python = venv_dir / "bin" / "python"
        pip = venv_dir / "bin" / "pip"

        # Install core deps only (no extras)
        subprocess.run(
            [str(pip), "install", "-e", str(project_root), "--quiet"],
            check=True,
            capture_output=True,
        )

        result = subprocess.run(
            [str(python), "-c", "import depthfusion.mcp.server; print('ok')"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"stdio import failed in clean-core venv.\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
        assert "ok" in result.stdout
