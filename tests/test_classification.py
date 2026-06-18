"""T-567: Policy unit tests — ClassificationLevel handling rules + label mapping.

Covers:
- All 4 classification levels and their handling rules
- Default-deny on unknown labels (must map to confidential or restricted, never public)
- Label mapping from M365/SharePoint labels to DepthFusion ClassificationLevel
- LabelMappingConfigError for invalid configs
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from depthfusion.authz.classification import (
    ClassificationLevel,
    HandlingRules,
    Role,
    CLASSIFICATION_POLICY,
    get_handling_rules,
)
from depthfusion.authz.label_mapping import (
    LabelMapper,
    LabelMappingConfigError,
    map_label,
)


# ===========================================================================
# Classification taxonomy + handling rules (T-565 coverage / T-567 gate)
# ===========================================================================


class TestClassificationLevels:
    """Verify the four-level taxonomy exists and has the expected values."""

    def test_all_four_levels_defined(self):
        values = {lvl.value for lvl in ClassificationLevel}
        assert values == {"public", "internal", "confidential", "restricted"}

    def test_all_levels_in_policy(self):
        for level in ClassificationLevel:
            assert level in CLASSIFICATION_POLICY, f"{level} missing from CLASSIFICATION_POLICY"

    @pytest.mark.parametrize(
        "level",
        list(ClassificationLevel),
        ids=lambda lvl: lvl.value,
    )
    def test_get_handling_rules_returns_typed_dict(self, level: ClassificationLevel):
        rules = get_handling_rules(level)
        assert isinstance(rules["export_allowed"], bool)
        assert isinstance(rules["cache_allowed"], bool)
        assert isinstance(rules["redact_in_search"], bool)
        assert isinstance(rules["allowed_roles"], list)
        assert len(rules["allowed_roles"]) > 0


class TestPublicLevel:
    def test_export_allowed(self):
        rules = get_handling_rules(ClassificationLevel.PUBLIC)
        assert rules["export_allowed"] is True

    def test_cache_allowed(self):
        rules = get_handling_rules(ClassificationLevel.PUBLIC)
        assert rules["cache_allowed"] is True

    def test_no_redaction_in_search(self):
        rules = get_handling_rules(ClassificationLevel.PUBLIC)
        assert rules["redact_in_search"] is False

    def test_external_role_allowed(self):
        rules = get_handling_rules(ClassificationLevel.PUBLIC)
        assert Role.EXTERNAL in rules["allowed_roles"]

    def test_all_roles_allowed(self):
        rules = get_handling_rules(ClassificationLevel.PUBLIC)
        expected = {Role.ADMIN, Role.DATA_ENGINEER, Role.ANALYST, Role.VIEWER, Role.EXTERNAL}
        assert set(rules["allowed_roles"]) == expected


class TestInternalLevel:
    def test_export_not_allowed(self):
        rules = get_handling_rules(ClassificationLevel.INTERNAL)
        assert rules["export_allowed"] is False

    def test_cache_allowed(self):
        rules = get_handling_rules(ClassificationLevel.INTERNAL)
        assert rules["cache_allowed"] is True

    def test_no_redaction_in_search(self):
        rules = get_handling_rules(ClassificationLevel.INTERNAL)
        assert rules["redact_in_search"] is False

    def test_external_role_not_allowed(self):
        rules = get_handling_rules(ClassificationLevel.INTERNAL)
        assert Role.EXTERNAL not in rules["allowed_roles"]

    def test_viewer_allowed(self):
        rules = get_handling_rules(ClassificationLevel.INTERNAL)
        assert Role.VIEWER in rules["allowed_roles"]


class TestConfidentialLevel:
    def test_export_not_allowed(self):
        rules = get_handling_rules(ClassificationLevel.CONFIDENTIAL)
        assert rules["export_allowed"] is False

    def test_cache_not_allowed(self):
        rules = get_handling_rules(ClassificationLevel.CONFIDENTIAL)
        assert rules["cache_allowed"] is False

    def test_redacted_in_search(self):
        rules = get_handling_rules(ClassificationLevel.CONFIDENTIAL)
        assert rules["redact_in_search"] is True

    def test_viewer_not_allowed(self):
        rules = get_handling_rules(ClassificationLevel.CONFIDENTIAL)
        assert Role.VIEWER not in rules["allowed_roles"]

    def test_external_not_allowed(self):
        rules = get_handling_rules(ClassificationLevel.CONFIDENTIAL)
        assert Role.EXTERNAL not in rules["allowed_roles"]

    def test_admin_and_data_engineer_allowed(self):
        rules = get_handling_rules(ClassificationLevel.CONFIDENTIAL)
        allowed = set(rules["allowed_roles"])
        assert Role.ADMIN in allowed
        assert Role.DATA_ENGINEER in allowed


class TestRestrictedLevel:
    def test_export_not_allowed(self):
        rules = get_handling_rules(ClassificationLevel.RESTRICTED)
        assert rules["export_allowed"] is False

    def test_cache_not_allowed(self):
        rules = get_handling_rules(ClassificationLevel.RESTRICTED)
        assert rules["cache_allowed"] is False

    def test_redacted_in_search(self):
        rules = get_handling_rules(ClassificationLevel.RESTRICTED)
        assert rules["redact_in_search"] is True

    def test_only_admin_allowed(self):
        rules = get_handling_rules(ClassificationLevel.RESTRICTED)
        assert rules["allowed_roles"] == [Role.ADMIN]

    @pytest.mark.parametrize(
        "role",
        [Role.DATA_ENGINEER, Role.ANALYST, Role.VIEWER, Role.EXTERNAL],
    )
    def test_non_admin_roles_not_allowed(self, role: Role):
        rules = get_handling_rules(ClassificationLevel.RESTRICTED)
        assert role not in rules["allowed_roles"]


# ===========================================================================
# Sensitivity ordering invariants
# ===========================================================================


class TestHandlingRuleInvariants:
    """Cross-level invariant checks: stricter levels must be strictly tighter."""

    @pytest.mark.parametrize(
        "more_sensitive, less_sensitive",
        [
            (ClassificationLevel.RESTRICTED, ClassificationLevel.CONFIDENTIAL),
            (ClassificationLevel.CONFIDENTIAL, ClassificationLevel.INTERNAL),
            (ClassificationLevel.INTERNAL, ClassificationLevel.PUBLIC),
        ],
        ids=lambda p: p.value if isinstance(p, ClassificationLevel) else str(p),
    )
    def test_stricter_level_has_fewer_or_equal_allowed_roles(
        self,
        more_sensitive: ClassificationLevel,
        less_sensitive: ClassificationLevel,
    ):
        rules_strict = get_handling_rules(more_sensitive)
        rules_loose = get_handling_rules(less_sensitive)
        strict_roles = set(rules_strict["allowed_roles"])
        loose_roles = set(rules_loose["allowed_roles"])
        assert strict_roles.issubset(loose_roles), (
            f"{more_sensitive.value} allows roles not allowed by {less_sensitive.value}: "
            f"{strict_roles - loose_roles}"
        )

    def test_public_allows_export_others_do_not(self):
        assert get_handling_rules(ClassificationLevel.PUBLIC)["export_allowed"] is True
        for level in [
            ClassificationLevel.INTERNAL,
            ClassificationLevel.CONFIDENTIAL,
            ClassificationLevel.RESTRICTED,
        ]:
            assert get_handling_rules(level)["export_allowed"] is False

    def test_only_public_and_internal_allow_caching(self):
        assert get_handling_rules(ClassificationLevel.PUBLIC)["cache_allowed"] is True
        assert get_handling_rules(ClassificationLevel.INTERNAL)["cache_allowed"] is True
        assert get_handling_rules(ClassificationLevel.CONFIDENTIAL)["cache_allowed"] is False
        assert get_handling_rules(ClassificationLevel.RESTRICTED)["cache_allowed"] is False


# ===========================================================================
# Default-deny: unknown labels must never resolve to public or internal
# ===========================================================================


class TestDefaultDenyOnUnknownLabels:
    """Unknown labels must default to confidential or restricted — never public/internal."""

    _SAFE_LEVELS = frozenset(
        {ClassificationLevel.CONFIDENTIAL, ClassificationLevel.RESTRICTED}
    )
    _UNSAFE_LEVELS = frozenset(
        {ClassificationLevel.PUBLIC, ClassificationLevel.INTERNAL}
    )

    @pytest.mark.parametrize(
        "unknown_label",
        [
            "totally-unknown-label",
            "SomeRandomLabel",
            "",
            "unknown-public-like",   # something that looks like public but isn't mapped
            "unknown-internal-like", # something that looks like internal but isn't mapped
            "HIGHLY SECRET",
            "Customer PII v3",
        ],
    )
    def test_unknown_labels_default_to_safe_level(self, unknown_label: str):
        mapper = LabelMapper()
        result = mapper.map(unknown_label)
        assert result in self._SAFE_LEVELS, (
            f"Label {unknown_label!r} mapped to {result.value} — "
            f"unknown labels must resolve to confidential or restricted, not {result.value}"
        )

    def test_unknown_label_never_resolves_to_public(self):
        mapper = LabelMapper()
        assert mapper.map("completely-unknown") != ClassificationLevel.PUBLIC

    def test_unknown_label_never_resolves_to_internal(self):
        mapper = LabelMapper()
        assert mapper.map("completely-unknown") != ClassificationLevel.INTERNAL

    def test_default_level_is_safe(self):
        mapper = LabelMapper()
        assert mapper.default_level in self._SAFE_LEVELS


# ===========================================================================
# Label mapping: M365 → DepthFusion levels
# ===========================================================================


class TestLabelMapper:
    """Parameterized tests for known M365 labels → expected ClassificationLevel."""

    @pytest.mark.parametrize(
        "label, expected_level",
        [
            # Public / General
            ("Public", ClassificationLevel.PUBLIC),
            ("General", ClassificationLevel.PUBLIC),
            ("Non-Business", ClassificationLevel.PUBLIC),
            # Internal
            ("General Business", ClassificationLevel.INTERNAL),
            ("Internal", ClassificationLevel.INTERNAL),
            ("Internal Use Only", ClassificationLevel.INTERNAL),
            ("Company Internal", ClassificationLevel.INTERNAL),
            # Confidential
            ("Confidential", ClassificationLevel.CONFIDENTIAL),
            ("Confidential - Finance", ClassificationLevel.CONFIDENTIAL),
            ("Confidential - Legal", ClassificationLevel.CONFIDENTIAL),
            ("Confidential - HR", ClassificationLevel.CONFIDENTIAL),
            ("PII", ClassificationLevel.CONFIDENTIAL),
            ("Personal Data", ClassificationLevel.CONFIDENTIAL),
            # Restricted
            ("Highly Confidential", ClassificationLevel.RESTRICTED),
            ("Secret", ClassificationLevel.RESTRICTED),
            ("Top Secret", ClassificationLevel.RESTRICTED),
            ("Restricted", ClassificationLevel.RESTRICTED),
        ],
    )
    def test_known_label_mapping(self, label: str, expected_level: ClassificationLevel):
        mapper = LabelMapper()
        assert mapper.map(label) == expected_level, (
            f"Label {label!r} expected {expected_level.value}, "
            f"got {mapper.map(label).value}"
        )

    def test_case_insensitive_matching(self):
        mapper = LabelMapper()
        assert mapper.map("CONFIDENTIAL") == ClassificationLevel.CONFIDENTIAL
        assert mapper.map("confidential") == ClassificationLevel.CONFIDENTIAL
        assert mapper.map("Confidential") == ClassificationLevel.CONFIDENTIAL
        assert mapper.map("cOnFiDeNtIaL") == ClassificationLevel.CONFIDENTIAL

    def test_case_insensitive_public(self):
        mapper = LabelMapper()
        assert mapper.map("PUBLIC") == ClassificationLevel.PUBLIC
        assert mapper.map("public") == ClassificationLevel.PUBLIC

    def test_is_known_true_for_configured_label(self):
        mapper = LabelMapper()
        assert mapper.is_known("Confidential") is True

    def test_is_known_false_for_unknown_label(self):
        mapper = LabelMapper()
        assert mapper.is_known("some-random-label-xyz") is False

    def test_is_known_case_insensitive(self):
        mapper = LabelMapper()
        assert mapper.is_known("CONFIDENTIAL") is True
        assert mapper.is_known("confidential") is True

    def test_known_labels_returns_list(self):
        mapper = LabelMapper()
        labels = mapper.known_labels()
        assert isinstance(labels, list)
        assert len(labels) > 0

    def test_known_labels_are_lower_cased(self):
        mapper = LabelMapper()
        for label in mapper.known_labels():
            assert label == label.lower(), f"Known label {label!r} is not lower-cased"


class TestLabelMapperWithCustomConfig:
    """Test LabelMapper with inline YAML config via tmp_path."""

    def _write_config(self, tmp_path: Path, content: str) -> Path:
        p = tmp_path / "label_mapping.yml"
        p.write_text(textwrap.dedent(content), encoding="utf-8")
        return p

    def test_custom_config_mapping(self, tmp_path: Path):
        cfg = self._write_config(
            tmp_path,
            """
            mappings:
              "My Public Label": public
              "My Secret": restricted
            default_level: confidential
            """,
        )
        mapper = LabelMapper(config_path=cfg)
        assert mapper.map("My Public Label") == ClassificationLevel.PUBLIC
        assert mapper.map("My Secret") == ClassificationLevel.RESTRICTED

    def test_unknown_label_uses_default_level_confidential(self, tmp_path: Path):
        cfg = self._write_config(
            tmp_path,
            """
            mappings:
              "Known": internal
            default_level: confidential
            """,
        )
        mapper = LabelMapper(config_path=cfg)
        assert mapper.map("Unknown Label") == ClassificationLevel.CONFIDENTIAL

    def test_unknown_label_uses_default_level_restricted(self, tmp_path: Path):
        cfg = self._write_config(
            tmp_path,
            """
            mappings:
              "Known": internal
            default_level: restricted
            """,
        )
        mapper = LabelMapper(config_path=cfg)
        assert mapper.map("Unknown Label") == ClassificationLevel.RESTRICTED

    def test_map_label_convenience_function(self, tmp_path: Path):
        cfg = self._write_config(
            tmp_path,
            """
            mappings:
              "Top Secret": restricted
            default_level: confidential
            """,
        )
        assert map_label("Top Secret", config_path=cfg) == ClassificationLevel.RESTRICTED
        assert map_label("Unknown", config_path=cfg) == ClassificationLevel.CONFIDENTIAL

    def test_config_path_property(self, tmp_path: Path):
        cfg = self._write_config(
            tmp_path,
            """
            mappings:
              "A": public
            default_level: confidential
            """,
        )
        mapper = LabelMapper(config_path=cfg)
        assert mapper.config_path == cfg

    def test_default_level_property(self, tmp_path: Path):
        cfg = self._write_config(
            tmp_path,
            """
            mappings: {}
            default_level: restricted
            """,
        )
        mapper = LabelMapper(config_path=cfg)
        assert mapper.default_level == ClassificationLevel.RESTRICTED


# ===========================================================================
# LabelMappingConfigError — invalid configs
# ===========================================================================


class TestLabelMappingConfigError:
    """Validation errors on malformed or unsafe YAML configs."""

    def _write_config(self, tmp_path: Path, content: str) -> Path:
        p = tmp_path / "label_mapping.yml"
        p.write_text(textwrap.dedent(content), encoding="utf-8")
        return p

    def test_invalid_default_level_public_raises(self, tmp_path: Path):
        cfg = self._write_config(
            tmp_path,
            """
            mappings:
              "A": confidential
            default_level: public
            """,
        )
        with pytest.raises(LabelMappingConfigError, match="default_level"):
            LabelMapper(config_path=cfg)

    def test_invalid_default_level_internal_raises(self, tmp_path: Path):
        cfg = self._write_config(
            tmp_path,
            """
            mappings:
              "A": confidential
            default_level: internal
            """,
        )
        with pytest.raises(LabelMappingConfigError, match="default_level"):
            LabelMapper(config_path=cfg)

    def test_unknown_classification_level_in_mappings_raises(self, tmp_path: Path):
        cfg = self._write_config(
            tmp_path,
            """
            mappings:
              "A": super_secret
            default_level: confidential
            """,
        )
        with pytest.raises(LabelMappingConfigError, match="super_secret"):
            LabelMapper(config_path=cfg)

    def test_missing_mappings_key_raises(self, tmp_path: Path):
        cfg = self._write_config(
            tmp_path,
            """
            default_level: confidential
            """,
        )
        with pytest.raises(LabelMappingConfigError, match="mappings"):
            LabelMapper(config_path=cfg)

    def test_malformed_yaml_raises(self, tmp_path: Path):
        p = tmp_path / "label_mapping.yml"
        p.write_text(":\nthis: is: not: valid: yaml: [\n", encoding="utf-8")
        with pytest.raises(LabelMappingConfigError):
            LabelMapper(config_path=p)

    def test_missing_file_raises(self, tmp_path: Path):
        missing = tmp_path / "does_not_exist.yml"
        with pytest.raises(LabelMappingConfigError, match="Cannot read"):
            LabelMapper(config_path=missing)

    def test_non_mapping_yaml_raises(self, tmp_path: Path):
        cfg = self._write_config(tmp_path, "- item1\n- item2\n")
        with pytest.raises(LabelMappingConfigError):
            LabelMapper(config_path=cfg)
