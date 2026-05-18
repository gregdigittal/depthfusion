# tests/test_retrieval/test_ciqs_a_regression.py
"""Regression guard for CIQS Category A (Retrieval Quality) failures.

Three CIQS A prompts that previously returned low scores due to:
  1. Cross-project contamination (depthfusion sessions outranking skillforge)
  2. Boilerplate-only blocks ranked at full weight
  3. Rules-file content never indexed

Test strategy: exercise the individual scoring helpers that drive the fix,
and verify the composite scoring arithmetic produces the expected ordering
on synthetic blocks that mirror real CIQS A retrieval scenarios.
"""
from __future__ import annotations

from depthfusion.retrieval.hybrid import (
    boilerplate_penalty,
    detect_mentioned_projects,
    extract_session_project,
    filter_blocks_by_project,
    lexical_richness_penalty,
)

# ---------------------------------------------------------------------------
# boilerplate_penalty — CIQS A1/A2/A3 fix: downrank envelope-only blocks
# ---------------------------------------------------------------------------

class TestBoilerplatePenalty:
    def test_empty_content_returns_one(self):
        assert boilerplate_penalty("") == 1.0

    def test_rich_session_content_no_penalty(self):
        content = "Implemented JWT refresh in middleware.\nDecision: use httpOnly cookies."
        assert boilerplate_penalty(content) == 1.0

    def test_short_session_start_only_penalised(self):
        """≤12 non-blank lines, all boilerplate → 0.2×."""
        content = (
            "--- SESSION START at 03:34:43 ---\n"
            "Project: skillforge\n"
            'End Reason: {"session_id":"abc","transcript_path":"/foo"}\n'
        )
        assert boilerplate_penalty(content) == 0.2

    def test_short_session_end_only_penalised(self):
        content = (
            "--- SESSION END at 09:54:40 ---\n"
            "Project: skillforge\n"
            'End Reason: {"session_id":"xyz","transcript_path":"/bar"}\n'
        )
        assert boilerplate_penalty(content) == 0.2

    def test_compaction_event_short_penalised(self):
        content = (
            "--- COMPACTION EVENT at 08:12:16 ---\n"
            "Project: depthfusion\n"
            "Directory: /home/gregmorris/projects/depthfusion\n"
            'Trigger: context limit reached\n'
            'Hook Input: {"session_id":"cb5c5db4"}\n'
        )
        assert boilerplate_penalty(content) == 0.2

    def test_long_block_with_boilerplate_header_no_penalty(self):
        """If block has >12 non-blank lines, even with a boilerplate header, no penalty.
        These are rich sessions with a compaction header prepended."""
        lines = ["--- SESSION START at 10:00:00 ---", "Project: skillforge"]
        lines += [f"Implemented feature step {i} in router." for i in range(20)]
        content = "\n".join(lines)
        assert boilerplate_penalty(content) == 1.0

    def test_exactly_twelve_lines_penalised(self):
        lines = ["--- SESSION END at 01:00:00 ---"] + [f"Line {i}" for i in range(11)]
        assert len([ln for ln in lines if ln.strip()]) == 12
        content = "\n".join(lines)
        assert boilerplate_penalty(content) == 0.2

    def test_thirteen_lines_no_penalty(self):
        lines = ["--- SESSION END at 01:00:00 ---"] + [f"Line {i}" for i in range(12)]
        assert len([ln for ln in lines if ln.strip()]) == 13
        content = "\n".join(lines)
        assert boilerplate_penalty(content) == 1.0


# ---------------------------------------------------------------------------
# lexical_richness_penalty — penalise low-TTR content (log dumps, templates)
# ---------------------------------------------------------------------------

class TestLexicalRichnessPenalty:
    def test_empty_returns_one(self):
        assert lexical_richness_penalty("") == 1.0

    def test_short_content_returns_one(self):
        # 5 unique word-tokens (length <=20) → no penalty
        assert lexical_richness_penalty("hello world foo bar") == 1.0

    def test_high_diversity_no_penalty(self):
        # Rich technical session — TTR well above 0.20
        content = (
            "Implemented JWT refresh middleware. Decision: use httpOnly cookies.\n"
            "Added eslint rule for no-unhandled-promise. Files: src/middleware/error.ts"
        )
        assert lexical_richness_penalty(content) == 1.0

    def test_low_diversity_penalised(self):
        # Very repetitive content — same words repeated
        content = ("INFO server started server ready server running " * 10).strip()
        result = lexical_richness_penalty(content)
        assert 0.5 <= result < 1.0

    def test_log_dump_penalised(self):
        # Log dump with repeated tokens
        content = " ".join(["ERROR", "WARNING", "INFO", "DEBUG"] * 20)
        result = lexical_richness_penalty(content)
        assert result < 1.0

    def test_rich_session_no_penalty(self):
        # A high-diversity technical block
        content = (
            "Refactored authentication module to use OAuth2 PKCE flow.\n"
            "Updated database schema with new indices on user_sessions table.\n"
            "Fixed race condition in token refresh endpoint.\n"
            "Migrated configuration to environment variables via dotenv.\n"
            "Wrote integration tests for callback handling and token validation.\n"
        )
        assert lexical_richness_penalty(content) == 1.0

    def test_boundary_at_ttr_floor(self):
        # Build content where exactly TTR = 0.20: 1 unique per 5 tokens
        # 40 total tokens, 8 unique (TTR = 8/40 = 0.20)
        words = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta"]
        content = " ".join(words * 5)  # 40 tokens, 8 unique → TTR=0.20
        result = lexical_richness_penalty(content)
        assert result == 1.0  # exactly at floor → no penalty

    def test_minimum_clamp_is_half(self):
        # All same word (TTR = 1/N → near 0) → must return >= 0.5
        content = ("word " * 50).strip()
        result = lexical_richness_penalty(content)
        assert result >= 0.5


# ---------------------------------------------------------------------------
# extract_session_project — CIQS A1: parse plain-text session headers
# ---------------------------------------------------------------------------

class TestExtractSessionProject:
    def test_extracts_project_from_session_end(self):
        content = (
            "--- SESSION END at 03:34:43 ---\n"
            "Project: skillforge\n"
            'End Reason: {"session_id":"097ee9a5"}\n'
        )
        assert extract_session_project(content) == "skillforge"

    def test_extracts_project_from_session_start(self):
        content = (
            "--- SESSION START at 02:44:49 ---\n"
            "Project: digittal-ccrs\n"
            "Directory: /home/gregmorris/projects/agreement-automation\n"
        )
        assert extract_session_project(content) == "digittal-ccrs"

    def test_extracts_project_from_compaction_event(self):
        content = (
            "--- COMPACTION EVENT at 08:12:16 ---\n"
            "Project: depthfusion\n"
            "Directory: /home/gregmorris/projects/depthfusion\n"
        )
        assert extract_session_project(content) == "depthfusion"

    def test_returns_none_for_empty_content(self):
        assert extract_session_project("") is None

    def test_returns_none_when_no_project_line(self):
        content = "Some session notes without a project header.\n"
        assert extract_session_project(content) is None

    def test_ignores_project_inside_json_payload(self):
        """JSON end-reason line may contain 'project' — should not match."""
        content = (
            "--- SESSION END at 03:34:43 ---\n"
            '{"project":"skillforge","session_id":"abc"}\n'
        )
        # The JSON line does NOT match ^Project:\s+ so returns None
        result = extract_session_project(content)
        assert result is None

    def test_strips_trailing_whitespace(self):
        content = "--- SESSION START at 01:00:00 ---\nProject:   agent-ops   \n"
        assert extract_session_project(content) == "agent-ops"


# ---------------------------------------------------------------------------
# detect_mentioned_projects — CIQS A1/A2: query-mention widening
# ---------------------------------------------------------------------------

class TestDetectMentionedProjects:
    _PROJECTS = {"depthfusion", "skillforge", "agent-ops", "digittal-ccrs"}

    def test_detects_skillforge_in_a1_prompt(self):
        """CIQS A1: 'TypeScript error handling in the SkillForge router'"""
        query = (
            "I'm working on TypeScript error handling in the SkillForge router. "
            "Based on my prior work and session history, what are the most "
            "relevant patterns, decisions, or warnings I should be aware of?"
        )
        result = detect_mentioned_projects(query, self._PROJECTS)
        assert "skillforge" in result

    def test_detects_skillforge_in_a2_prompt(self):
        """CIQS A2: 'Adding new step types to the SkillForge Skill IR'"""
        query = (
            "I'm working on Adding new step types to the SkillForge Skill IR. "
            "Based on my prior work and session history, what are the most "
            "relevant patterns, decisions, or warnings I should be aware of?"
        )
        result = detect_mentioned_projects(query, self._PROJECTS)
        assert "skillforge" in result

    def test_does_not_detect_unmentioned_projects(self):
        query = "I'm working on TypeScript error handling in the SkillForge router."
        result = detect_mentioned_projects(query, self._PROJECTS)
        assert "depthfusion" not in result
        assert "agent-ops" not in result

    def test_cross_project_query_no_slug_returns_empty(self):
        """CIQS A3: commit message style — no project slug in query."""
        query = (
            "I'm working on My preferences for commit message style and PR structure. "
            "Based on my prior work and session history, what are the most "
            "relevant patterns, decisions, or warnings I should be aware of?"
        )
        result = detect_mentioned_projects(query, self._PROJECTS)
        assert result == frozenset()

    def test_empty_query_returns_empty(self):
        assert detect_mentioned_projects("", self._PROJECTS) == frozenset()

    def test_empty_available_projects_returns_empty(self):
        assert detect_mentioned_projects("SkillForge router", set()) == frozenset()

    def test_short_slug_ignored(self):
        """Slugs under 4 chars should not be matched (avoids false positives)."""
        short_projects = {"df", "sa"}
        query = "I'm working on df sa things"
        result = detect_mentioned_projects(query, short_projects)
        assert result == frozenset()

    def test_case_insensitive_match(self):
        query = "Working on SKILLFORGE code"
        result = detect_mentioned_projects(query, self._PROJECTS)
        assert "skillforge" in result


# ---------------------------------------------------------------------------
# filter_blocks_by_project — extra_projects widening (CIQS A1/A2 integration)
# ---------------------------------------------------------------------------

def _session_block(chunk_id: str, project: str | None) -> dict:
    content = (
        f"--- SESSION END at 03:00:00 ---\nProject: {project}\nsome content\n"
        if project
        else "--- SESSION END at 03:00:00 ---\nsome content\n"
    )
    return {
        "chunk_id": chunk_id,
        "source": "session",
        "project": project,
        "content": content,
    }


class TestExtraProjectsWidening:
    def test_extra_project_included_when_not_current(self):
        """When query mentions 'skillforge' while in 'depthfusion', skillforge blocks included."""
        blocks = [
            _session_block("df1", "depthfusion"),
            _session_block("sf1", "skillforge"),
            _session_block("ao1", "agent-ops"),
        ]
        result = filter_blocks_by_project(
            blocks,
            current_project="depthfusion",
            cross_project=False,
            extra_projects=frozenset({"skillforge"}),
        )
        chunk_ids = {b["chunk_id"] for b in result}
        assert "df1" in chunk_ids
        assert "sf1" in chunk_ids
        assert "ao1" not in chunk_ids  # not mentioned, not current

    def test_no_extra_projects_filters_strictly(self):
        """Without extra_projects, only current project blocks returned."""
        blocks = [
            _session_block("df1", "depthfusion"),
            _session_block("sf1", "skillforge"),
        ]
        result = filter_blocks_by_project(
            blocks,
            current_project="depthfusion",
            cross_project=False,
            extra_projects=None,
        )
        chunk_ids = {b["chunk_id"] for b in result}
        assert "df1" in chunk_ids
        assert "sf1" not in chunk_ids

    def test_extra_projects_none_is_backward_compatible(self):
        """Existing callers without extra_projects kwarg should behave unchanged."""
        blocks = [
            _session_block("df1", "depthfusion"),
            _session_block("sf1", "skillforge"),
        ]
        result = filter_blocks_by_project(
            blocks,
            current_project="depthfusion",
            cross_project=False,
        )
        chunk_ids = {b["chunk_id"] for b in result}
        assert "df1" in chunk_ids
        assert "sf1" not in chunk_ids


# ---------------------------------------------------------------------------
# Composite scoring scenario — mirrors CIQS A1 conditions
# ---------------------------------------------------------------------------

class TestCompositeScoringOrderingA1:
    """Verify that, given equal BM25 raw scores, a short boilerplate depthfusion
    block scores lower than a content-rich skillforge block after applying the
    new scoring factors.

    Source weights: session=0.70
    Boilerplate penalty: 0.2× for short envelope blocks
    Mention boost: 2.0× when block.project in query

    Expected ordering: sf_rich > df_boilerplate
    """

    _SOURCE_WEIGHT_SESSION = 0.70
    _BOILERPLATE_PENALTY_SHORT = 0.2
    _MENTION_BOOST_MATCH = 2.0
    _MENTION_BOOST_NO_MATCH = 1.0

    def _score(self, content: str, project: str, query: str) -> float:
        bp = boilerplate_penalty(content)
        mentioned = detect_mentioned_projects(query, {"depthfusion", "skillforge"})
        mb = self._MENTION_BOOST_MATCH if project in mentioned else self._MENTION_BOOST_NO_MATCH
        raw_bm25 = 1.0  # equal raw scores to isolate our factors
        recency_boost = 1.0  # neutralised for this test
        return raw_bm25 * self._SOURCE_WEIGHT_SESSION * recency_boost * bp * mb

    def test_skillforge_rich_outranks_depthfusion_boilerplate(self):
        """CIQS A1: SkillForge router query — content-rich skillforge block
        must outrank boilerplate-only depthfusion block."""
        query = "TypeScript error handling in the SkillForge router"

        df_boilerplate_content = (
            "--- SESSION END at 07:14:20 ---\n"
            "Project: depthfusion\n"
        )
        sf_rich_content = (
            "--- SESSION END at 03:34:43 ---\n"
            "Project: skillforge\n"
            "Implemented JWT error middleware. Used express-async-errors.\n"
            "Decision: throw typed AppError subclasses, catch in root handler.\n"
            "Added eslint rule for no-unhandled-promise.\n"
            "Files: src/middleware/error.ts, src/router/auth.ts\n"
            "Reviewed TypeScript strict mode settings.\n"
        )

        score_df = self._score(df_boilerplate_content, "depthfusion", query)
        score_sf = self._score(sf_rich_content, "skillforge", query)

        assert score_sf > score_df, (
            f"Expected skillforge rich ({score_sf:.4f}) > "
            f"depthfusion boilerplate ({score_df:.4f})"
        )

    def test_skillforge_rich_a2_outranks_agent_ops_boilerplate(self):
        """CIQS A2: SkillForge Skill IR query — same pattern, different session slugs."""
        query = "Adding new step types to the SkillForge Skill IR"

        ao_boilerplate = (
            "--- SESSION END at 03:40:58 ---\n"
            "Project: agent-ops\n"
        )
        sf_rich = (
            "--- SESSION END at 03:34:43 ---\n"
            "Project: skillforge\n"
            "Added LoopStep and ForkStep to skill IR. Updated YAML serializer.\n"
            "Decision: discriminated union on 'type' field.\n"
            "SkillChain model updated to accept new step variants.\n"
            "Tests in tests/ir/test_step_types.py.\n"
            "Reference: docs/skill-ir-spec.md\n"
        )

        available = {"agent-ops", "skillforge"}
        bp_ao = boilerplate_penalty(ao_boilerplate)
        bp_sf = boilerplate_penalty(sf_rich)
        mentioned = detect_mentioned_projects(query, available)
        mb_ao = self._MENTION_BOOST_MATCH if "agent-ops" in mentioned else self._MENTION_BOOST_NO_MATCH
        mb_sf = self._MENTION_BOOST_MATCH if "skillforge" in mentioned else self._MENTION_BOOST_NO_MATCH

        score_ao = 1.0 * self._SOURCE_WEIGHT_SESSION * 1.0 * bp_ao * mb_ao
        score_sf = 1.0 * self._SOURCE_WEIGHT_SESSION * 1.0 * bp_sf * mb_sf

        assert score_sf > score_ao, (
            f"Expected skillforge rich ({score_sf:.4f}) > "
            f"agent-ops boilerplate ({score_ao:.4f})"
        )
