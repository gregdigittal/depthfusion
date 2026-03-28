import pytest
from pathlib import Path
from depthfusion.capture.auto_learn import HeuristicExtractor, extract_key_decisions


SAMPLE_SESSION = """\
# Goal: implement user auth
## Progress
- Task 1: DONE — added JWT middleware
→ Decision: use RS256 not HS256 for JWT signing
NOTE: refresh tokens stored in httpOnly cookies only
IMPORTANT: never log the JWT payload
WARNING: session.tmp files are cleared on compact

## Key Findings
**ANTHROPIC_API_KEY** must be set in systemd EnvironmentFile

## Architecture
- Chose PostgreSQL over SQLite for concurrent writes
"""

CORRUPT_SESSION = "}\x00\x01invalid\xff"
EMPTY_SESSION = "   \n\n  "


def test_extract_decisions_from_valid_content():
    decisions = extract_key_decisions(SAMPLE_SESSION)
    assert len(decisions) > 0
    # Should capture → decision arrow lines
    assert any("RS256" in d for d in decisions)
    # Should capture NOTE: lines
    assert any("httpOnly" in d for d in decisions)


def test_extract_decisions_from_empty_content():
    decisions = extract_key_decisions(EMPTY_SESSION)
    assert decisions == []


def test_extract_decisions_from_corrupt_content():
    # Should not raise, should return empty or partial
    decisions = extract_key_decisions(CORRUPT_SESSION)
    assert isinstance(decisions, list)


def test_heuristic_extractor_from_file(tmp_path):
    session_file = tmp_path / "2026-03-28-goal-test.tmp"
    session_file.write_text(SAMPLE_SESSION, encoding="utf-8")
    extractor = HeuristicExtractor()
    output = extractor.extract_from_file(session_file)
    assert output is not None
    assert "RS256" in output or "JWT" in output


def test_heuristic_extractor_skips_empty_file(tmp_path):
    empty_file = tmp_path / "empty.tmp"
    empty_file.write_text(EMPTY_SESSION, encoding="utf-8")
    extractor = HeuristicExtractor()
    output = extractor.extract_from_file(empty_file)
    assert output is None


def test_heuristic_extractor_file_not_found():
    extractor = HeuristicExtractor()
    output = extractor.extract_from_file(Path("/nonexistent/file.tmp"))
    assert output is None
