# tests/test_graph/test_dedup.py
"""T-620: entity-linker deduplication — near-duplicate merge tests.

Tests confirm:
  * near-duplicates of the same type are merged (case/whitespace/suffix variants)
  * distinct entities remain separate
  * merged nodes carry unioned source_files
  * merged nodes record a merge_confidence in metadata
  * ACL handling is fail-closed (intersection, not union)
  * threshold is configurable
  * deterministic: repeated calls produce the same canonical set
  * no LLM / network required
"""
from __future__ import annotations

import pytest

from depthfusion.graph.extractor import make_entity_id
from depthfusion.graph.linker import EntityDeduplicator
from depthfusion.graph.types import Entity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ent(
    name: str,
    etype: str = "concept",
    conf: float = 0.85,
    project: str = "depthfusion",
    source: str = "docs/a.md",
    acl: list[str] | None = None,
) -> Entity:
    return Entity(
        entity_id=make_entity_id(name, etype, project),
        name=name,
        type=etype,
        project=project,
        source_files=[source],
        confidence=conf,
        first_seen="2026-03-28T00:00:00",
        metadata={"acl_allow": list(acl) if acl else [project]},
    )


# ---------------------------------------------------------------------------
# Near-duplicate merging — the examples required by the AC
# ---------------------------------------------------------------------------


def test_bm25_near_duplicate_merges() -> None:
    """'BM25' and 'BM25 scoring' share one token; Jaccard = 1/2 = 0.5.

    At the default threshold of 0.85 they are DISTINCT (below threshold).
    With a threshold of 0.4 they merge.  This documents the boundary case
    explicitly so future threshold changes are noticed.
    """
    # Default threshold: 0.85 — distinct (BM25 vs BM25 scoring is a half-overlap)
    strict = EntityDeduplicator(threshold=0.85)
    ents = [_ent("BM25"), _ent("BM25 scoring")]
    assert len(strict.deduplicate(ents)) == 2

    # Loose threshold: 0.4 — merges (Jaccard 0.5 >= 0.4)
    loose = EntityDeduplicator(threshold=0.4)
    assert len(loose.deduplicate(ents)) == 1


def test_case_variants_merge_at_default_threshold() -> None:
    """'BM25', 'bm25', 'Bm25' all normalize to 'bm25' (same token) → merge.

    Note: 'BM 25' (with a space) normalizes to 'bm 25' (two tokens 'bm' and
    '25') which has zero Jaccard overlap with 'bm25' (one token).  Case
    changes within a single run of alphanumeric chars ARE equivalent.
    """
    dedup = EntityDeduplicator(threshold=0.85)
    ents = [_ent("BM25"), _ent("bm25"), _ent("Bm25")]
    merged = dedup.deduplicate(ents)
    assert len(merged) == 1


def test_whitespace_punctuation_variants_merge() -> None:
    """Trailing punctuation and extra whitespace are stripped by normalizer."""
    dedup = EntityDeduplicator(threshold=0.85)
    ents = [_ent("Acme Corp"), _ent("acme corp"), _ent("ACME  Corp.")]
    merged = dedup.deduplicate(ents)
    assert len(merged) == 1


def test_function_suffix_stripped_for_comparison() -> None:
    """'rrf_fuse()' and 'rrf fuse' both normalize to 'rrf fuse' → merge."""
    dedup = EntityDeduplicator(threshold=0.85)
    ents = [_ent("rrf_fuse()", etype="function"), _ent("rrf fuse", etype="function")]
    merged = dedup.deduplicate(ents)
    assert len(merged) == 1


# ---------------------------------------------------------------------------
# Distinct entities remain separate
# ---------------------------------------------------------------------------


def test_distinct_entities_stay_separate() -> None:
    dedup = EntityDeduplicator(threshold=0.85)
    ents = [_ent("Acme Corporation"), _ent("Beta Industries")]
    merged = dedup.deduplicate(ents)
    assert len(merged) == 2


def test_same_name_different_type_stays_separate() -> None:
    """Type mismatch prevents merge regardless of name similarity."""
    dedup = EntityDeduplicator(threshold=0.85)
    ents = [_ent("parser", etype="concept"), _ent("parser", etype="function")]
    merged = dedup.deduplicate(ents)
    assert len(merged) == 2


# ---------------------------------------------------------------------------
# source_files are unioned on merge
# ---------------------------------------------------------------------------


def test_source_files_unioned_on_merge() -> None:
    dedup = EntityDeduplicator(threshold=0.85)
    a = _ent("Acme Corp", source="docs/a.md")
    b = _ent("acme corp", source="docs/b.md")
    merged = dedup.deduplicate([a, b])
    assert len(merged) == 1
    assert set(merged[0].source_files) == {"docs/a.md", "docs/b.md"}


def test_source_files_no_duplicates_in_union() -> None:
    """Same source file mentioned in both entities appears only once."""
    dedup = EntityDeduplicator(threshold=0.85)
    a = _ent("Acme Corp", source="docs/shared.md")
    b = _ent("acme corp", source="docs/shared.md")
    merged = dedup.deduplicate([a, b])
    assert merged[0].source_files.count("docs/shared.md") == 1


# ---------------------------------------------------------------------------
# merge_confidence is recorded in metadata
# ---------------------------------------------------------------------------


def test_merge_confidence_recorded_on_identical_names() -> None:
    """Identical normalized names → similarity 1.0 → merge_confidence == 1.0."""
    dedup = EntityDeduplicator(threshold=0.85)
    a = _ent("Acme Corp", source="docs/a.md")
    b = _ent("acme corp", source="docs/b.md")
    merged = dedup.deduplicate([a, b])
    assert merged[0].metadata.get("merge_confidence") == pytest.approx(1.0)


def test_merge_confidence_absent_for_unmerged_entity() -> None:
    """A lone entity (no merge) has no merge_confidence entry."""
    dedup = EntityDeduplicator(threshold=0.85)
    merged = dedup.deduplicate([_ent("Acme Corp")])
    assert "merge_confidence" not in merged[0].metadata


def test_merge_confidence_keeps_highest_across_multiple_merges() -> None:
    """Three-way merge: merge_confidence tracks the maximum similarity seen.

    Survivor: "Acme Group Ltd" (first entity, name never changes).
    Second entity "Acme Group" → Jaccard({acme,group,ltd}, {acme,group}) = 2/3 ≈ 0.667.
    Third entity "acme group ltd" → identical normalized → similarity = 1.0.
    Final merge_confidence must be 1.0 (the maximum).
    """
    dedup = EntityDeduplicator(threshold=0.4)
    a = _ent("Acme Group Ltd", source="docs/a.md")
    b = _ent("Acme Group", source="docs/b.md")         # sim ≈ 0.667
    c = _ent("acme group ltd", source="docs/c.md")     # sim = 1.0
    merged = dedup.deduplicate([a, b, c])
    assert len(merged) == 1
    assert merged[0].metadata["merge_confidence"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# confidence (entity-level) is max on merge
# ---------------------------------------------------------------------------


def test_entity_confidence_is_max_on_merge() -> None:
    dedup = EntityDeduplicator(threshold=0.85)
    a = _ent("Acme Corp", conf=0.70)
    b = _ent("acme corp", conf=0.95)
    merged = dedup.deduplicate([a, b])
    assert merged[0].confidence == pytest.approx(0.95)


# ---------------------------------------------------------------------------
# ACL — fail-closed (intersection)
# ---------------------------------------------------------------------------


def test_acl_intersection_shared_principal_survives() -> None:
    """Principals present in ALL source ACLs survive the merge."""
    dedup = EntityDeduplicator(threshold=0.85)
    a = _ent("Acme Corp", acl=["acme-corp", "shared-team"])
    b = _ent("acme corp", acl=["legal-team", "shared-team"])
    merged = dedup.deduplicate([a, b])
    assert len(merged) == 1
    assert set(merged[0].metadata["acl_allow"]) == {"shared-team"}


def test_acl_intersection_disjoint_yields_empty() -> None:
    """Disjoint ACLs → empty intersection.

    The merged node becomes inaccessible via the store's required-ACL
    invariant.  This is the correct fail-closed outcome: the caller must
    resolve the conflict rather than the deduplicator silently widening
    access.
    """
    dedup = EntityDeduplicator(threshold=0.85)
    a = _ent("Acme Corp", acl=["acme-corp"])
    b = _ent("acme corp", acl=["legal-team"])
    merged = dedup.deduplicate([a, b])
    assert len(merged) == 1
    assert merged[0].metadata["acl_allow"] == []


def test_acl_intersection_does_not_widen_access() -> None:
    """Fail-closed invariant: no principal in the merged ACL that was absent
    from the survivor's original ACL."""
    dedup = EntityDeduplicator(threshold=0.85)
    a = _ent("Acme Corp", acl=["acme-corp"])
    b = _ent("acme corp", acl=["legal-team", "acme-corp"])
    merged = dedup.deduplicate([a, b])
    acl = set(merged[0].metadata["acl_allow"])
    # "legal-team" should NOT appear — it was not in acme-corp's ACL
    assert "legal-team" not in acl
    assert "acme-corp" in acl


def test_acl_same_principals_preserved_after_merge() -> None:
    """When both entities share the same ACL, the merged node retains it."""
    dedup = EntityDeduplicator(threshold=0.85)
    a = _ent("Acme Corp", acl=["acme-corp", "admin"])
    b = _ent("acme corp", acl=["acme-corp", "admin"])
    merged = dedup.deduplicate([a, b])
    assert set(merged[0].metadata["acl_allow"]) == {"acme-corp", "admin"}


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_dedup_is_deterministic() -> None:
    """Calling deduplicate twice on the same input produces the same result."""
    import copy

    dedup = EntityDeduplicator(threshold=0.85)
    ents1 = [_ent("Acme Corp", source="a.md"), _ent("acme corp", source="b.md")]
    ents2 = copy.deepcopy(ents1)
    r1 = dedup.deduplicate(ents1)
    r2 = dedup.deduplicate(ents2)
    assert r1[0].name == r2[0].name
    assert r1[0].source_files == r2[0].source_files
    assert r1[0].metadata["acl_allow"] == r2[0].metadata["acl_allow"]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_dedup_empty_input() -> None:
    assert EntityDeduplicator().deduplicate([]) == []


def test_dedup_single_entity_unchanged() -> None:
    dedup = EntityDeduplicator(threshold=0.85)
    ent = _ent("Acme Corp", acl=["acme-corp"])
    merged = dedup.deduplicate([ent])
    assert len(merged) == 1
    assert merged[0].metadata.get("merge_confidence") is None


def test_dedup_no_network_or_llm_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deduplication must run fully offline — no anthropic / network calls."""
    import sys

    # Prevent any import of anthropic during this test
    monkeypatch.setitem(sys.modules, "anthropic", None)  # type: ignore[arg-type]

    dedup = EntityDeduplicator(threshold=0.85)
    ents = [_ent("Acme Corp"), _ent("acme corp")]
    merged = dedup.deduplicate(ents)
    assert len(merged) == 1
