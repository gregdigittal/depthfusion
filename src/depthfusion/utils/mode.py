"""Central mode normalisation for DEPTHFUSION_MODE."""
from __future__ import annotations

import warnings

_CANONICAL_MODES = frozenset({"local", "vps-cpu", "vps-gpu", "mac-mlx"})
_ALIAS_MAP = {"vps": "vps-cpu"}  # legacy alias


def normalise_mode(raw: str | None) -> str:
    """Return canonical mode string from raw env value.

    Canonical outputs: "local", "vps-cpu", "vps-gpu", "mac-mlx".
    Legacy "vps" maps to "vps-cpu" with a DeprecationWarning.
    Unknown values fall back to "local" with a warning.
    """
    value = (raw or "local").strip().lower()
    if value in _ALIAS_MAP:
        warnings.warn(
            f"DEPTHFUSION_MODE={value!r} is deprecated; use 'vps-cpu' instead.",
            DeprecationWarning,
            stacklevel=3,
        )
        value = _ALIAS_MAP[value]
    if value not in _CANONICAL_MODES:
        import logging
        logging.getLogger(__name__).warning(
            "Unknown DEPTHFUSION_MODE=%r — falling back to 'local'.", value
        )
        value = "local"
    return value
