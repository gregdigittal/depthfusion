"""T-563: Discovery front-matter ACL parser + writer tests.

parse_acl(content) -> ACLFrontmatter
write_acl(content, acl) -> str

Frontmatter format:
---
acl_allow:
  - principal_id
classification: internal
---
"""
from __future__ import annotations

import pytest

from depthfusion.authz.frontmatter import ACLFrontmatter, parse_acl, write_acl


# ---------------------------------------------------------------------------
# parse_acl tests
# ---------------------------------------------------------------------------


class TestParseAcl:
    def test_block_list_acl_allow(self):
        content = "---\nacl_allow:\n  - greg\n  - group:admins\nclassification: internal\n---\nContent here.\n"
        result = parse_acl(content)
        assert result.acl_allow == ["greg", "group:admins"]
        assert result.classification == "internal"

    def test_single_principal_block(self):
        content = "---\nacl_allow:\n  - greg\nclassification: public\n---\nBody.\n"
        result = parse_acl(content)
        assert result.acl_allow == ["greg"]
        assert result.classification == "public"

    def test_flow_list_acl_allow(self):
        content = "---\nacl_allow: [greg, group:data-science]\nclassification: confidential\n---\n"
        result = parse_acl(content)
        assert result.acl_allow == ["greg", "group:data-science"]
        assert result.classification == "confidential"

    def test_scalar_acl_allow(self):
        content = "---\nacl_allow: greg\nclassification: internal\n---\n"
        result = parse_acl(content)
        assert result.acl_allow == ["greg"]

    def test_no_frontmatter_returns_defaults(self):
        content = "Just a plain markdown document.\n"
        result = parse_acl(content)
        assert result.acl_allow == ["greg"]
        assert result.classification == "internal"

    def test_missing_acl_allow_returns_default(self):
        content = "---\ndate: 2026-06-11\nclassification: internal\n---\nContent.\n"
        result = parse_acl(content)
        assert result.acl_allow == ["greg"]
        assert result.classification == "internal"

    def test_missing_classification_returns_default(self):
        content = "---\nacl_allow:\n  - greg\n---\nContent.\n"
        result = parse_acl(content)
        assert result.classification == "internal"

    def test_restricted_classification(self):
        content = "---\nacl_allow:\n  - group:admins\nclassification: restricted\n---\n"
        result = parse_acl(content)
        assert result.classification == "restricted"

    def test_confidential_classification(self):
        content = "---\nacl_allow:\n  - greg\nclassification: confidential\n---\n"
        result = parse_acl(content)
        assert result.classification == "confidential"

    def test_public_classification(self):
        content = "---\nacl_allow: ['*']\nclassification: public\n---\n"
        result = parse_acl(content)
        assert result.classification == "public"

    def test_invalid_classification_raises(self):
        content = "---\nacl_allow:\n  - greg\nclassification: top-secret\n---\n"
        with pytest.raises(ValueError, match="classification must be one of"):
            parse_acl(content)

    def test_empty_frontmatter_returns_defaults(self):
        content = "---\n---\nDocument body.\n"
        result = parse_acl(content)
        assert result.acl_allow == ["greg"]
        assert result.classification == "internal"

    def test_other_frontmatter_fields_ignored(self):
        content = "---\ndate: 2026-06-11\nproject: depthfusion\nacl_allow:\n  - greg\nclassification: internal\ntags:\n  - v2\n---\nContent.\n"
        result = parse_acl(content)
        assert result.acl_allow == ["greg"]
        assert result.classification == "internal"

    def test_wildcard_acl_allow(self):
        content = "---\nacl_allow:\n  - '*'\nclassification: public\n---\n"
        result = parse_acl(content)
        # The star should be parsed as a principal entry
        assert "*" in result.acl_allow


# ---------------------------------------------------------------------------
# ACLFrontmatter model tests
# ---------------------------------------------------------------------------


class TestACLFrontmatter:
    def test_defaults(self):
        acl = ACLFrontmatter()
        assert acl.acl_allow == ["greg"]
        assert acl.classification == "internal"

    def test_valid_classification_values(self):
        for cls in ("public", "internal", "confidential", "restricted"):
            acl = ACLFrontmatter(acl_allow=["greg"], classification=cls)
            assert acl.classification == cls

    def test_invalid_classification_raises(self):
        with pytest.raises(ValueError, match="classification must be one of"):
            ACLFrontmatter(acl_allow=["greg"], classification="unknown")


# ---------------------------------------------------------------------------
# write_acl tests
# ---------------------------------------------------------------------------


class TestWriteAcl:
    def test_adds_frontmatter_to_document_without_it(self):
        content = "# My Discovery\n\nSome text.\n"
        acl = ACLFrontmatter(acl_allow=["greg"], classification="internal")
        result = write_acl(content, acl)
        assert result.startswith("---\n")
        assert "acl_allow:" in result
        assert "- greg" in result
        assert "classification: internal" in result
        assert "# My Discovery" in result

    def test_updates_existing_frontmatter_acl(self):
        content = "---\ndate: 2026-06-11\nacl_allow:\n  - old-user\nclassification: public\n---\nBody.\n"
        acl = ACLFrontmatter(acl_allow=["greg", "group:admins"], classification="confidential")
        result = write_acl(content, acl)
        # New values present
        assert "greg" in result
        assert "group:admins" in result
        assert "confidential" in result
        # Old values gone
        assert "old-user" not in result
        assert "public" not in result
        # Other fields preserved
        assert "date: 2026-06-11" in result

    def test_preserves_body_after_frontmatter(self):
        content = "---\nacl_allow:\n  - greg\nclassification: internal\n---\n\n# Title\n\nParagraph.\n"
        acl = ACLFrontmatter(acl_allow=["greg"], classification="internal")
        result = write_acl(content, acl)
        assert "# Title" in result
        assert "Paragraph." in result

    def test_roundtrip(self):
        content = "---\ndate: 2026-06-11\n---\nContent.\n"
        acl = ACLFrontmatter(acl_allow=["greg", "group:data-science"], classification="confidential")
        written = write_acl(content, acl)
        parsed = parse_acl(written)
        assert parsed.acl_allow == acl.acl_allow
        assert parsed.classification == acl.classification

    def test_write_multiple_principals(self):
        content = "# Doc\n"
        acl = ACLFrontmatter(acl_allow=["alice", "bob", "group:admins"], classification="restricted")
        result = write_acl(content, acl)
        assert "alice" in result
        assert "bob" in result
        assert "group:admins" in result
        assert "restricted" in result

    def test_write_then_parse_no_frontmatter(self):
        content = "Plain document with no frontmatter.\n"
        acl = ACLFrontmatter(acl_allow=["greg"], classification="internal")
        written = write_acl(content, acl)
        parsed = parse_acl(written)
        assert parsed.acl_allow == ["greg"]
        assert parsed.classification == "internal"
        assert "Plain document" in written

    def test_idempotent_write(self):
        """Writing the same ACL twice yields the same result."""
        content = "---\ndate: 2026-06-11\n---\nBody.\n"
        acl = ACLFrontmatter(acl_allow=["greg"], classification="internal")
        first = write_acl(content, acl)
        second = write_acl(first, acl)
        # Parse both — should be equivalent
        p1 = parse_acl(first)
        p2 = parse_acl(second)
        assert p1.acl_allow == p2.acl_allow
        assert p1.classification == p2.classification
