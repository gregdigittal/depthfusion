"""Discovery front-matter ACL parser and writer.

T-563: parse_acl(content) -> ACLFrontmatter
       write_acl(content, acl) -> str

Frontmatter format:
---
acl_allow:
  - principal_id
classification: internal
---

Classification values: public | internal | confidential | restricted
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_VALID_CLASSIFICATIONS = {"public", "internal", "confidential", "restricted"}

_FRONTMATTER_RE = re.compile(
    r"^---[ \t]*\n(.*?)\n---[ \t]*(?:\n|$)", re.DOTALL
)

# Matches "acl_allow:" followed by a YAML list (block or flow) or a single value.
_ACL_ALLOW_BLOCK_RE = re.compile(
    r"^acl_allow:\s*\n((?:[ \t]+-[ \t]+\S.*\n?)+)",
    re.MULTILINE,
)
_ACL_ALLOW_FLOW_RE = re.compile(
    r"^acl_allow:\s*\[([^\]]*)\]",
    re.MULTILINE,
)
_ACL_ALLOW_SCALAR_RE = re.compile(
    r"^acl_allow:\s*(\S+.*)",
    re.MULTILINE,
)

_CLASSIFICATION_RE = re.compile(
    r"^classification:\s*(\S+)",
    re.MULTILINE,
)


@dataclass
class ACLFrontmatter:
    """Parsed ACL fields from a discovery document's YAML frontmatter."""

    acl_allow: list[str] = field(default_factory=lambda: ["greg"])
    classification: str = "internal"

    def __post_init__(self) -> None:
        if self.classification not in _VALID_CLASSIFICATIONS:
            raise ValueError(
                f"classification must be one of {sorted(_VALID_CLASSIFICATIONS)!r}. "
                f"Got: {self.classification!r}"
            )


def _parse_acl_allow(fm_body: str) -> list[str]:
    """Extract acl_allow list from a frontmatter body string.

    Supports:
      - Block YAML list:  acl_allow:\\n  - foo\\n  - bar
      - Flow YAML list:   acl_allow: [foo, bar]
      - Scalar (single):  acl_allow: foo
    """
    # Block list
    m = _ACL_ALLOW_BLOCK_RE.search(fm_body)
    if m:
        items = re.findall(r"^[ \t]+-[ \t]+(\S+.*)", m.group(1), re.MULTILINE)
        result = [i.strip().strip("\"'") for i in items if i.strip()]
        return result or ["greg"]

    # Flow list
    m = _ACL_ALLOW_FLOW_RE.search(fm_body)
    if m:
        items = [i.strip().strip("\"'") for i in m.group(1).split(",")]
        result = [i for i in items if i]
        return result or ["greg"]

    # Scalar
    m = _ACL_ALLOW_SCALAR_RE.search(fm_body)
    if m:
        val = m.group(1).strip().strip("\"'")
        if val:
            return [val]

    return ["greg"]


def _parse_classification(fm_body: str) -> str:
    m = _CLASSIFICATION_RE.search(fm_body)
    if m:
        return m.group(1).strip().strip("\"'")
    return "internal"


def parse_acl(content: str) -> ACLFrontmatter:
    """Parse ACL fields from a Markdown document with YAML frontmatter.

    Args:
        content: Full text of a Markdown discovery file, optionally starting
                 with a ``---`` … ``---`` YAML frontmatter block.

    Returns:
        ACLFrontmatter with acl_allow and classification.
        Defaults to acl_allow=["greg"], classification="internal" when fields
        are absent from the frontmatter.

    Raises:
        ValueError: If classification value is not one of the valid enum values.
    """
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return ACLFrontmatter()

    fm_body = m.group(1)
    acl_allow = _parse_acl_allow(fm_body)
    classification = _parse_classification(fm_body)

    # Validate classification on read — reject unknown values rather than silently default.
    if classification not in _VALID_CLASSIFICATIONS:
        raise ValueError(
            f"classification must be one of {sorted(_VALID_CLASSIFICATIONS)!r}. "
            f"Got: {classification!r}"
        )

    return ACLFrontmatter(acl_allow=acl_allow, classification=classification)


def write_acl(content: str, acl: ACLFrontmatter) -> str:
    """Write (or update) ACL fields in a Markdown document's YAML frontmatter.

    If the document already has frontmatter, the acl_allow and classification
    fields are upserted (added or replaced). Other frontmatter fields are
    preserved unchanged.

    If the document has no frontmatter, a new frontmatter block is prepended.

    Args:
        content: Full text of the Markdown document.
        acl: ACLFrontmatter to stamp into the document.

    Returns:
        Modified document text with acl_allow and classification set.
    """
    # Build the ACL lines to inject / replace.
    allow_lines = "\n".join(f"  - {p}" for p in acl.acl_allow)
    acl_block = f"acl_allow:\n{allow_lines}\nclassification: {acl.classification}"

    m = _FRONTMATTER_RE.match(content)
    if not m:
        # No frontmatter — prepend a new block.
        return f"---\n{acl_block}\n---\n{content}"

    fm_body = m.group(1)

    # Remove existing acl_allow (block, flow, or scalar) and classification lines.
    # We replace the entire acl_allow section (which may span multiple lines for block style).
    fm_body = _ACL_ALLOW_BLOCK_RE.sub("", fm_body)
    fm_body = _ACL_ALLOW_FLOW_RE.sub("", fm_body)
    fm_body = _ACL_ALLOW_SCALAR_RE.sub("", fm_body)
    fm_body = _CLASSIFICATION_RE.sub("", fm_body)

    # Strip trailing blank lines within the body.
    fm_body = fm_body.strip()

    # Reassemble: existing (non-ACL) frontmatter + ACL fields.
    if fm_body:
        new_fm_body = f"{fm_body}\n{acl_block}"
    else:
        new_fm_body = acl_block

    rest = content[m.end():]
    return f"---\n{new_fm_body}\n---\n{rest}"
