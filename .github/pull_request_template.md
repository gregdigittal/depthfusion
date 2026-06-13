## Summary
<!-- What does this PR do? Link the ticket: V2 T-NNN -->

## Acceptance criteria
<!-- Paste the ACs from the ticket spec; check each one -->
- [ ] AC-1:
- [ ] AC-2:

## Test evidence
<!-- Output of: pytest tests/<package>/ -x -q -->

## Review checklist
- [ ] ruff passes (ruff check .)
- [ ] mypy passes (python -m mypy src/ --ignore-missing-imports)
- [ ] Tests green and coverage ≥ 80%
- [ ] No new public bindings added without Bearer token gate
- [ ] No port published to 0.0.0.0 without justification comment + auth + firewall
