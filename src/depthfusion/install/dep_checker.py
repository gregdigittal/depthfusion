"""Dependency status checker for the guided install wizard (S-110 T-368)."""
from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python <3.11
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

_KNOWN_MODES = frozenset({"local", "vps-cpu", "vps-gpu", "mac-mlx"})

# Fallback when pyproject.toml is not accessible (e.g. installed package).
_HARDCODED_EXTRAS: dict[str, list[str]] = {
    "local": [],
    "vps-cpu": [
        "anthropic>=0.40",
        "chromadb>=0.4",
        "fastapi>=0.100",
        "uvicorn>=0.23",
    ],
    "vps-gpu": [
        "sentence-transformers>=2.2",
        "chromadb>=0.4",
        "fastapi>=0.100",
        "uvicorn>=0.23",
    ],
    "mac-mlx": [
        "mlx-lm>=0.18",
        "sentence-transformers>=2.2",
        "chromadb>=0.4",
        "fastapi>=0.100",
        "uvicorn>=0.23",
    ],
}

# dep_checker.py → install/ → depthfusion/ → src/ → project root
_PYPROJECT_PATH = Path(__file__).parents[3] / "pyproject.toml"


def _load_mode_extras() -> dict[str, list[str]]:
    """Parse optional-dependencies from pyproject.toml; fall back to hardcoded."""
    if tomllib is None or not _PYPROJECT_PATH.exists():
        return dict(_HARDCODED_EXTRAS)
    try:
        with open(_PYPROJECT_PATH, "rb") as f:
            data = tomllib.load(f)
        raw = data.get("project", {}).get("optional-dependencies", {})
        return {k: [str(v) for v in raw.get(k, _HARDCODED_EXTRAS.get(k, []))] for k in _KNOWN_MODES}
    except Exception:
        return dict(_HARDCODED_EXTRAS)


def _parse_req(req: str) -> tuple[str, str]:
    """Split a PEP 508 requirement into (dist_name, version_spec).

    Strips environment markers and extras before parsing.
    """
    req = req.strip().split(";")[0].strip()
    req = re.sub(r"\[.*?\]", "", req).strip()
    m = re.match(r"^([A-Za-z0-9_.\-]+)(.*)", req)
    if not m:
        return req, ""
    return m.group(1).strip(), m.group(2).strip()


def check_deps(mode: str) -> list[dict]:
    """Return dep status for the given mode.

    Each dict: {
        "name": str,
        "required": bool,
        "installed": bool,
        "installed_version": str | None,
        "required_version": str,
    }

    Raises ValueError for unknown mode.
    """
    if mode not in _KNOWN_MODES:
        raise ValueError(
            f"Unknown mode {mode!r}. Valid modes: {sorted(_KNOWN_MODES)}"
        )

    extras = _load_mode_extras()
    results: list[dict] = []

    for req_str in extras.get(mode, []):
        name, version_spec = _parse_req(req_str)
        try:
            installed_ver: str | None = pkg_version(name)
            installed = True
        except PackageNotFoundError:
            installed_ver = None
            installed = False

        results.append({
            "name": name,
            "required": True,
            "installed": installed,
            "installed_version": installed_ver,
            "required_version": version_spec,
        })

    return results
