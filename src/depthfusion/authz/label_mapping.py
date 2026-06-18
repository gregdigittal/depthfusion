"""Microsoft Sensitivity Label → DepthFusion ClassificationLevel mapping.

T-566: Sensitivity-label mapping config + validation

Maps Microsoft Information Protection (MIP) sensitivity labels (as found on
SharePoint / Microsoft 365 content) to DepthFusion ``ClassificationLevel``
values using a user-editable YAML config file.

Design rules:
- Config file: ``config/label_mapping.yml`` (relative to the repo root, or
  resolved via ``DEPTHFUSION_LABEL_MAPPING`` env var).
- Matching is **case-insensitive** — labels are normalised to lower-case before
  lookup.
- Labels absent from the mapping default to ``ClassificationLevel.CONFIDENTIAL``
  (safe default-deny; never resolves to ``public`` or ``internal`` for unknowns).
- The ``default_level`` key in the YAML must be ``confidential`` or
  ``restricted``; any other value raises ``LabelMappingConfigError`` at load
  time.
- Malformed YAML or a missing ``mappings`` key raises ``LabelMappingConfigError``.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Final

import yaml

from depthfusion.authz.classification import ClassificationLevel

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default config path, relative to the repository root.
_DEFAULT_CONFIG_RELATIVE: Final[str] = "config/label_mapping.yml"

#: Environment variable that overrides the config path.
_ENV_VAR: Final[str] = "DEPTHFUSION_LABEL_MAPPING"

#: Allowed values for ``default_level`` in the YAML.
_SAFE_DEFAULTS: Final[frozenset[str]] = frozenset(
    {ClassificationLevel.CONFIDENTIAL.value, ClassificationLevel.RESTRICTED.value}
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LabelMappingConfigError(ValueError):
    """Raised when the label-mapping YAML is missing, malformed, or invalid."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_config_path() -> Path:
    """Return the path to the label mapping YAML file.

    Priority:
    1. ``DEPTHFUSION_LABEL_MAPPING`` environment variable (absolute or relative
       to cwd).
    2. ``<repo_root>/config/label_mapping.yml`` — resolved by walking up from
       this file's location to find the ``config/`` directory.
    """
    env = os.environ.get(_ENV_VAR)
    if env:
        return Path(env).expanduser().resolve()

    # Walk up from this module's directory to find <repo_root>/config/
    here = Path(__file__).resolve().parent
    for ancestor in [here, *here.parents]:
        candidate = ancestor / _DEFAULT_CONFIG_RELATIVE
        if candidate.exists():
            return candidate

    # Fallback: cwd-relative (useful in tests)
    return Path(_DEFAULT_CONFIG_RELATIVE).resolve()


def _parse_config(path: Path) -> tuple[dict[str, ClassificationLevel], ClassificationLevel]:
    """Parse the YAML mapping file.

    Returns
    -------
    tuple[dict[str, ClassificationLevel], ClassificationLevel]
        A tuple of (normalised_mapping, default_level) where
        ``normalised_mapping`` keys are lower-cased label strings.

    Raises
    ------
    LabelMappingConfigError
        On I/O errors, YAML parse errors, missing keys, or invalid
        ``default_level`` values.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LabelMappingConfigError(
            f"Cannot read label mapping config at {path}: {exc}"
        ) from exc

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise LabelMappingConfigError(
            f"YAML parse error in {path}: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise LabelMappingConfigError(
            f"Label mapping config must be a YAML mapping at top level, got {type(data).__name__}"
        )

    # ── Validate default_level ────────────────────────────────────────────
    raw_default = data.get("default_level", ClassificationLevel.CONFIDENTIAL.value)
    if raw_default not in _SAFE_DEFAULTS:
        raise LabelMappingConfigError(
            f"Invalid default_level {raw_default!r} in {path}. "
            f"Must be one of: {sorted(_SAFE_DEFAULTS)}"
        )
    default_level = ClassificationLevel(raw_default)

    # ── Parse mappings ────────────────────────────────────────────────────
    raw_mappings = data.get("mappings")
    if raw_mappings is None:
        raise LabelMappingConfigError(
            f"Missing 'mappings' key in label mapping config {path}"
        )
    if not isinstance(raw_mappings, dict):
        raise LabelMappingConfigError(
            f"'mappings' in {path} must be a YAML mapping, got {type(raw_mappings).__name__}"
        )

    normalised: dict[str, ClassificationLevel] = {}
    for label, level_str in raw_mappings.items():
        label_key = str(label).lower()
        try:
            normalised[label_key] = ClassificationLevel(str(level_str))
        except ValueError:
            raise LabelMappingConfigError(
                f"Unknown ClassificationLevel {level_str!r} for label {label!r} in {path}. "
                f"Valid values: {[v.value for v in ClassificationLevel]}"
            )

    return normalised, default_level


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class LabelMapper:
    """Maps Microsoft sensitivity labels to DepthFusion ClassificationLevel.

    Parameters
    ----------
    config_path:
        Path to the ``label_mapping.yml`` file.  If *None*, the path is
        resolved automatically (env var → repo root → cwd fallback).

    Examples
    --------
    >>> mapper = LabelMapper()
    >>> mapper.map("Confidential")
    <ClassificationLevel.CONFIDENTIAL: 'confidential'>
    >>> mapper.map("Unknown label")
    <ClassificationLevel.CONFIDENTIAL: 'confidential'>
    """

    def __init__(self, config_path: Path | str | None = None) -> None:
        path = Path(config_path).resolve() if config_path is not None else _resolve_config_path()
        self._mappings, self._default_level = _parse_config(path)
        self._config_path = path

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def default_level(self) -> ClassificationLevel:
        """The fallback ClassificationLevel for unlisted labels."""
        return self._default_level

    @property
    def config_path(self) -> Path:
        """Path to the YAML config file that was loaded."""
        return self._config_path

    # ------------------------------------------------------------------
    # Core mapping
    # ------------------------------------------------------------------

    def map(self, label: str) -> ClassificationLevel:
        """Map a Microsoft sensitivity *label* to a ClassificationLevel.

        Matching is case-insensitive.  Labels not present in the config
        return ``self.default_level`` (which is always ``confidential`` or
        ``restricted`` — never ``public`` or ``internal``).

        Parameters
        ----------
        label:
            The raw sensitivity label string from SharePoint / M365.

        Returns
        -------
        ClassificationLevel
            The corresponding classification level.
        """
        return self._mappings.get(label.lower(), self._default_level)

    def is_known(self, label: str) -> bool:
        """Return True if *label* has an explicit entry in the config.

        Parameters
        ----------
        label:
            The raw sensitivity label string (case-insensitive).
        """
        return label.lower() in self._mappings

    def known_labels(self) -> list[str]:
        """Return all known label strings (original casing normalised to lower)."""
        return list(self._mappings.keys())


def map_label(
    label: str,
    *,
    config_path: Path | str | None = None,
) -> ClassificationLevel:
    """Convenience function: map a single Microsoft sensitivity label.

    Creates a ``LabelMapper`` on every call — use ``LabelMapper`` directly
    when mapping many labels to amortise the YAML parsing cost.

    Parameters
    ----------
    label:
        The Microsoft sensitivity label string.
    config_path:
        Optional path override for the YAML config.

    Returns
    -------
    ClassificationLevel
        ``confidential`` (or ``restricted``) for unknown labels; the
        configured level for known ones.
    """
    return LabelMapper(config_path=config_path).map(label)


__all__ = [
    "LabelMapper",
    "LabelMappingConfigError",
    "map_label",
]
