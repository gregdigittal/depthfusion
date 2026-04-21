"""Unit tests for scripts/mine_session_prompts.py."""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest


SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"


@pytest.fixture(scope="module")
def mod():
    """Load scripts/mine_session_prompts.py as a module."""
    path = SCRIPTS_DIR / "mine_session_prompts.py"
    spec = importlib.util.spec_from_file_location("mine_session_prompts", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["mine_session_prompts"] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# Filter helpers
# --------------------------------------------------------------------------

class TestIsWrapperMessage:
    def test_command_message_wrapper(self, mod):
        assert mod.is_wrapper_message("<command-message>goal</command-message>\n...")

    def test_system_reminder_wrapper(self, mod):
        assert mod.is_wrapper_message("<system-reminder>\nThe TodoWrite tool...")

    def test_local_command_stdout_wrapper(self, mod):
        assert mod.is_wrapper_message("<local-command-stdout>output here</local-command-stdout>")

    def test_leading_whitespace_stripped_before_check(self, mod):
        # Real Claude Code sessions sometimes have leading whitespace
        assert mod.is_wrapper_message("   \n  <system-reminder>...")

    def test_user_prompt_is_not_wrapper(self, mod):
        assert not mod.is_wrapper_message("Please implement the login feature")

    def test_prompt_containing_tag_but_not_starting_with_it(self, mod):
        # User might legitimately mention these tags in a prompt
        assert not mod.is_wrapper_message(
            "How do I handle <command-message> tags in my parser?"
        )


class TestNormaliseForHash:
    def test_lowercase(self, mod):
        assert mod.normalise_for_hash("Hello World") == "hello world"

    def test_whitespace_collapse(self, mod):
        assert mod.normalise_for_hash("foo   bar\n\nbaz") == "foo bar baz"

    def test_trailing_whitespace_stripped(self, mod):
        assert mod.normalise_for_hash("  hello  ") == "hello"

    def test_two_near_duplicates_collide(self, mod):
        a = mod.normalise_for_hash("Please   proceed\n")
        b = mod.normalise_for_hash("  please PROCEED  ")
        assert a == b


# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------

class TestRedaction:
    def test_no_pattern_returns_unchanged(self, mod):
        text, n = mod.apply_redaction("hello world", None)
        assert text == "hello world"
        assert n == 0

    def test_anthropic_key_redacted(self, mod):
        pattern = re.compile(mod._DEFAULT_REDACT)
        text, n = mod.apply_redaction(
            "My key is sk-ant-api03-abcdef1234567890abcdef1234567890",
            pattern,
        )
        assert "sk-ant-api03" not in text
        assert "[REDACTED]" in text
        assert n == 1

    def test_aws_key_redacted(self, mod):
        pattern = re.compile(mod._DEFAULT_REDACT)
        text, n = mod.apply_redaction(
            "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE",
            pattern,
        )
        assert "AKIAIOSFODNN7EXAMPLE" not in text
        assert n == 1

    def test_github_pat_redacted(self, mod):
        pattern = re.compile(mod._DEFAULT_REDACT)
        text, n = mod.apply_redaction(
            "ghp_" + "a" * 36,
            pattern,
        )
        assert "ghp_aaa" not in text
        assert n == 1

    def test_slack_token_redacted(self, mod):
        pattern = re.compile(mod._DEFAULT_REDACT)
        text, n = mod.apply_redaction(
            "xoxb-12345-67890-abcdefghijklmnopqrstuvwxyz1234567890AB",
            pattern,
        )
        assert "[REDACTED]" in text
        assert n == 1

    def test_multiple_matches_counted(self, mod):
        pattern = re.compile(mod._DEFAULT_REDACT)
        # Both keys are AKIA + exactly 16 A-Z0-9 chars (AWS spec = 20 total)
        key_a = "AKIA" + "A" * 16
        key_b = "AKIA" + "B" * 16
        text, n = mod.apply_redaction(f"first {key_a} then {key_b}", pattern)
        assert n == 2

    def test_normal_code_not_redacted(self, mod):
        pattern = re.compile(mod._DEFAULT_REDACT)
        prompt = "Please add a function that returns the sum of two integers"
        text, n = mod.apply_redaction(prompt, pattern)
        assert text == prompt
        assert n == 0


# --------------------------------------------------------------------------
# File extraction
# --------------------------------------------------------------------------

class TestExtractUserPrompts:
    def _write_session(self, path: Path, records: list[dict]) -> None:
        with path.open("w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

    def test_string_content_extracted(self, mod, tmp_path):
        p = tmp_path / "s1.jsonl"
        self._write_session(p, [
            {
                "type": "user",
                "message": {"role": "user", "content": "Please implement feature X"},
                "sessionId": "abc123",
                "timestamp": "2026-04-20T12:00:00Z",
            },
        ])
        results = list(mod.extract_user_prompts_from_file(p, "test-proj", min_chars=10))
        assert len(results) == 1
        assert results[0]["prompt"] == "Please implement feature X"
        assert results[0]["session_id"] == "abc123"
        assert results[0]["project_slug"] == "test-proj"

    def test_array_content_dropped(self, mod, tmp_path):
        # Tool results come as array content — must be dropped
        p = tmp_path / "s2.jsonl"
        self._write_session(p, [
            {
                "type": "user",
                "message": {"role": "user", "content": [{"type": "tool_result", "content": "..."}]},
            },
        ])
        results = list(mod.extract_user_prompts_from_file(p, "x", min_chars=10))
        assert results == []

    def test_non_user_types_dropped(self, mod, tmp_path):
        p = tmp_path / "s3.jsonl"
        self._write_session(p, [
            {"type": "assistant", "message": {"content": "some reply"}},
            {"type": "system", "content": "..."},
            {"type": "queue-operation", "content": "some op"},
            {"type": "attachment", "content": "..."},
        ])
        assert list(mod.extract_user_prompts_from_file(p, "x", min_chars=1)) == []

    def test_too_short_dropped(self, mod, tmp_path):
        p = tmp_path / "s4.jsonl"
        self._write_session(p, [
            {"type": "user", "message": {"content": "yes"}},
            {"type": "user", "message": {"content": "This is a longer actual prompt"}},
        ])
        results = list(mod.extract_user_prompts_from_file(p, "x", min_chars=20))
        assert len(results) == 1
        assert results[0]["prompt"] == "This is a longer actual prompt"

    def test_wrapper_messages_dropped(self, mod, tmp_path):
        p = tmp_path / "s5.jsonl"
        self._write_session(p, [
            {"type": "user", "message": {"content": "<command-message>goal</command-message>\nmore content here"}},
            {"type": "user", "message": {"content": "<system-reminder>\nSomething about a tool..."}},
            {"type": "user", "message": {"content": "Actual user prompt please do the thing"}},
        ])
        results = list(mod.extract_user_prompts_from_file(p, "x", min_chars=10))
        assert len(results) == 1
        assert results[0]["prompt"].startswith("Actual user")

    def test_malformed_json_skipped_not_crashed(self, mod, tmp_path):
        p = tmp_path / "s6.jsonl"
        p.write_text(
            '{"type":"user","message":{"content":"first valid"}}\n'
            'this is not json at all\n'
            '{"type":"user","message":{"content":"second valid prompt here"}}\n'
        )
        results = list(mod.extract_user_prompts_from_file(p, "x", min_chars=5))
        assert len(results) == 2

    def test_missing_file_returns_nothing_not_crashed(self, mod, tmp_path):
        missing = tmp_path / "nonexistent.jsonl"
        results = list(mod.extract_user_prompts_from_file(missing, "x", min_chars=10))
        assert results == []


# --------------------------------------------------------------------------
# End-to-end pipeline
# --------------------------------------------------------------------------

class TestPipelineEndToEnd:
    def _mk_session(self, path: Path, records: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

    def test_dedup_exact(self, mod, tmp_path):
        self._mk_session(tmp_path / "proj-a" / "s1.jsonl", [
            {"type": "user", "message": {"content": "Please fix the login bug"}},
            {"type": "user", "message": {"content": "Please fix the login bug"}},  # dup
            {"type": "user", "message": {"content": "PLEASE FIX THE LOGIN BUG"}},  # case dup
            {"type": "user", "message": {"content": "Unique prompt number two here"}},
        ])
        kept, stats = mod.mine_prompts(
            sessions_dir=tmp_path,
            min_chars=5,
            redact_pattern=None,
        )
        assert stats["kept"] == 2
        assert stats["dropped_duplicate"] == 2

    def test_project_filter(self, mod, tmp_path):
        self._mk_session(tmp_path / "proj-alpha" / "s.jsonl", [
            {"type": "user", "message": {"content": "Prompt from alpha project here"}},
        ])
        self._mk_session(tmp_path / "proj-beta" / "s.jsonl", [
            {"type": "user", "message": {"content": "Prompt from beta project here"}},
        ])
        kept, stats = mod.mine_prompts(
            sessions_dir=tmp_path,
            min_chars=5,
            redact_pattern=None,
            project_filter="alpha",
        )
        assert len(kept) == 1
        assert "alpha" in kept[0]["prompt"]
        # M-4 fix: the dropped_project_filter stat must actually count the
        # beta file that was filtered out. Previously it was initialised
        # to 0 and never incremented, making the summary misleading.
        assert stats["dropped_project_filter"] == 1

    def test_redaction_in_pipeline(self, mod, tmp_path):
        self._mk_session(tmp_path / "proj-a" / "s.jsonl", [
            {"type": "user",
             "message": {"content": "Here is my key: sk-ant-api03-secret12345678901234567890"}},
        ])
        pattern = re.compile(mod._DEFAULT_REDACT)
        kept, stats = mod.mine_prompts(
            sessions_dir=tmp_path,
            min_chars=5,
            redact_pattern=pattern,
        )
        assert len(kept) == 1
        assert "[REDACTED]" in kept[0]["prompt"]
        assert "sk-ant" not in kept[0]["prompt"]
        assert stats["redactions_applied"] >= 1

    def test_recursive_walk(self, mod, tmp_path):
        # Subagent transcripts nested under <project>/<uuid>/subagents/
        self._mk_session(tmp_path / "proj-a" / "uuid-1" / "subagents" / "agent-x.jsonl", [
            {"type": "user", "message": {"content": "Subagent prompt goes here"}},
        ])
        self._mk_session(tmp_path / "proj-a" / "main.jsonl", [
            {"type": "user", "message": {"content": "Main session prompt"}},
        ])
        kept, stats = mod.mine_prompts(
            sessions_dir=tmp_path,
            min_chars=5,
            redact_pattern=None,
        )
        assert stats["files_scanned"] == 2
        assert stats["kept"] == 2

    def test_project_slug_extracted(self, mod, tmp_path):
        self._mk_session(tmp_path / "my-project-slug" / "s.jsonl", [
            {"type": "user", "message": {"content": "Some prompt text here"}},
        ])
        kept, _ = mod.mine_prompts(
            sessions_dir=tmp_path,
            min_chars=5,
            redact_pattern=None,
        )
        assert kept[0]["project_slug"] == "my-project-slug"

    def test_nonexistent_sessions_dir_graceful(self, mod, tmp_path):
        kept, stats = mod.mine_prompts(
            sessions_dir=tmp_path / "does-not-exist",
            min_chars=5,
            redact_pattern=None,
        )
        assert kept == []
        assert stats["files_scanned"] == 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

class TestMainCLI:
    def test_cli_writes_output_file(self, mod, tmp_path):
        sessions = tmp_path / "sessions"
        (sessions / "proj").mkdir(parents=True)
        (sessions / "proj" / "s.jsonl").write_text(
            json.dumps({"type": "user",
                        "message": {"content": "Please implement the thing"}}) + "\n"
        )
        out = tmp_path / "corpus.jsonl"
        rc = mod.main([
            "--sessions-dir", str(sessions),
            "--min-chars", "5",
            "--out", str(out),
        ])
        assert rc == 0
        lines = out.read_text().strip().split("\n")
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert "Please implement" in rec["prompt"]

    def test_cli_invalid_regex_returns_error(self, mod, tmp_path):
        rc = mod.main([
            "--sessions-dir", str(tmp_path),
            "--redact", "[unclosed",
        ])
        assert rc == 2
