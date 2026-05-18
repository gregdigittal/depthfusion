# CIQS Scoring Template - local / run 2

> Generated: 2026-05-18
> DepthFusion version: unknown

Fill in an integer score (0-10) in each `score: ` line.
Categories with `retrieval-only: true` have blocks from DepthFusion below the prompt.
Other categories require running the prompt through Claude Code in a fresh session
and scoring the response against the rubric.

---

## A / A1 - Retrieval Quality

**Prompt:**

```
I'm working on TypeScript error handling in the SkillForge router. Based on my prior work and session history, what are the most
relevant patterns, decisions, or warnings I should be aware of? Please be specific about
what you recall and rate your confidence in each item.
```

retrieval-only: True

**Retrieved 5 blocks:**

1. `session` / `2026-05-18-skillforge-session` (score=41.773)
   > --- SESSION START at 05:30:55 ---
Project: skillforge
Directory: /home/gregmorris/projects/skillforge
--- SESSION START at 05:30:55 ---
Project: skillforge
Directory: /home/gregmorris/projects/skillforge
--- SESSION START at 05:30:55 ---
Pr
2. `session` / `2026-05-17-skillforge-session` (score=35.8672)
   > --- COMPACTION EVENT at 03:53:27 ---
Project: skillforge
Directory: /home/gregmorris/projects/skillforge
Trigger: context limit reached
Hook Input: {"session_id":"097ee9a5-0a7b-4aaf-99c4-fb37a9c5b437","transcript_path":"/home/gregmorris/.cl
3. `session` / `2026-05-16-skillforge-session` (score=31.6622)
   > # Session: 2026-05-16
**Date:** 2026-05-16
**Started:** 20:33
**Last Updated:** 21:01
**Project:** skillforge
**Branch:** main
**Worktree:** /home/gregmorris/projects/skillforge

---


--- COMPACTION EVENT at 10:07:15 ---
Project: skillforg
4. `session` / `2026-05-17-depthfusion-session` (score=27.988)
   > --- COMPACTION EVENT at 03:52:06 ---
Project: depthfusion
Directory: /home/gregmorris/projects/depthfusion
Trigger: context limit reached
Hook Input: {"session_id":"e00bf648-afe7-4179-9e6b-74316b665098","transcript_path":"/home/gregmorris/.
5. `session` / `2026-05-18-tito-apps-session` (score=24.9934)
   > --- COMPACTION EVENT at 05:31:20 ---
Project: tito-apps
Directory: /home/gregmorris/projects/tito-apps
Trigger: context limit reached
Hook Input: {"session_id":"ba38b370-9394-43a1-bdec-8194d0dee4e8","transcript_path":"/home/gregmorris/.clau

**Scores (0-10 each):**

- relevance: `score: 5`
- specificity: `score: 3`
- confidence_calibration: `score: 6`
- novel_signal: `score: 2`

**Notes:** 

---

## A / A2 - Retrieval Quality

**Prompt:**

```
I'm working on Adding new step types to the SkillForge Skill IR. Based on my prior work and session history, what are the most
relevant patterns, decisions, or warnings I should be aware of? Please be specific about
what you recall and rate your confidence in each item.
```

retrieval-only: True

**Retrieved 5 blocks:**

1. `session` / `2026-05-18-skillforge-session` (score=45.3323)
   > --- SESSION START at 05:30:55 ---
Project: skillforge
Directory: /home/gregmorris/projects/skillforge
--- SESSION START at 05:30:55 ---
Project: skillforge
Directory: /home/gregmorris/projects/skillforge
--- SESSION START at 05:30:55 ---
Pr
2. `session` / `2026-05-17-skillforge-session` (score=38.9823)
   > --- COMPACTION EVENT at 03:53:27 ---
Project: skillforge
Directory: /home/gregmorris/projects/skillforge
Trigger: context limit reached
Hook Input: {"session_id":"097ee9a5-0a7b-4aaf-99c4-fb37a9c5b437","transcript_path":"/home/gregmorris/.cl
3. `session` / `2026-05-16-skillforge-session` (score=36.9703)
   > # Session: 2026-05-16
**Date:** 2026-05-16
**Started:** 20:33
**Last Updated:** 21:01
**Project:** skillforge
**Branch:** main
**Worktree:** /home/gregmorris/projects/skillforge

---


--- COMPACTION EVENT at 10:07:15 ---
Project: skillforg
4. `session` / `2026-05-17-depthfusion-session` (score=28.8973)
   > --- COMPACTION EVENT at 03:52:06 ---
Project: depthfusion
Directory: /home/gregmorris/projects/depthfusion
Trigger: context limit reached
Hook Input: {"session_id":"e00bf648-afe7-4179-9e6b-74316b665098","transcript_path":"/home/gregmorris/.
5. `session` / `2026-05-18-depthfusion-session` (score=27.6398)
   > # Session: 2026-05-18
**Date:** 2026-05-18
**Started:** 05:39
**Last Updated:** 08:20
**Project:** depthfusion
**Branch:** main
**Worktree:** /home/gregmorris/projects/depthfusion

---


--- SESSION END at 05:45:19 ---
Project: depthfusion


**Scores (0-10 each):**

- relevance: `score: 6`
- specificity: `score: 4`
- confidence_calibration: `score: 7`
- novel_signal: `score: 3`

**Notes:** 

---

## A / A3 - Retrieval Quality

**Prompt:**

```
I'm working on My preferences for commit message style and PR structure. Based on my prior work and session history, what are the most
relevant patterns, decisions, or warnings I should be aware of? Please be specific about
what you recall and rate your confidence in each item.
```

retrieval-only: True

**Retrieved 5 blocks:**

1. `session` / `2026-05-18-tito-apps-session` (score=26.6174)
   > --- COMPACTION EVENT at 05:31:20 ---
Project: tito-apps
Directory: /home/gregmorris/projects/tito-apps
Trigger: context limit reached
Hook Input: {"session_id":"ba38b370-9394-43a1-bdec-8194d0dee4e8","transcript_path":"/home/gregmorris/.clau
2. `rule` / `commit-review#3` (score=26.1124)
   > ## The 3-tier workflow

### Tier 1 — Codex + Opus consensus review

Invoke the `i-auditreviewer-consensus` skill on the staged diff. That skill already implements:
- Round 0: Codex availability gate (via `codex:setup`)
- Round 1: independen
3. `session` / `2026-05-17-depthfusion-session` (score=26.0648)
   > --- COMPACTION EVENT at 03:52:06 ---
Project: depthfusion
Directory: /home/gregmorris/projects/depthfusion
Trigger: context limit reached
Hook Input: {"session_id":"e00bf648-afe7-4179-9e6b-74316b665098","transcript_path":"/home/gregmorris/.
4. `session` / `2026-05-18-depthfusion-session` (score=23.7151)
   > # Session: 2026-05-18
**Date:** 2026-05-18
**Started:** 05:39
**Last Updated:** 08:20
**Project:** depthfusion
**Branch:** main
**Worktree:** /home/gregmorris/projects/depthfusion

---


--- SESSION END at 05:45:19 ---
Project: depthfusion

5. `rule` / `agent-hub-pm-contract#4` (score=22.5139)
   > ## Dispatch Workflow Protocol

Use the Dispatch skill (invoke via the Skill tool or prefix your instruction with `/dispatch`). The skill reads `~/.dispatch/config.yaml` for model configuration; if missing, it self-configures on first use.



**Scores (0-10 each):**

- relevance: `score: 3`
- specificity: `score: 2`
- confidence_calibration: `score: 4`
- novel_signal: `score: 1`

**Notes:** 

---

## B / B1 - Code Quality

**Prompt:**

```
Review this code snippet and identify any issues. Be specific about severity:

Function missing try/catch around external API call (security.md violation)
```

retrieval-only: False

**Scores (0-10 each):**

- issue_detection: `score: `
- standards_citation: `score: `
- fix_quality: `score: `
- false_positives: `score: `

**Notes:** 

---

## B / B2 - Code Quality

**Prompt:**

```
Review this code snippet and identify any issues. Be specific about severity:

Switch on discriminated union missing a case (TypeScript exhaustiveness)
```

retrieval-only: False

**Scores (0-10 each):**

- issue_detection: `score: `
- standards_citation: `score: `
- fix_quality: `score: `
- false_positives: `score: `

**Notes:** 

---

## B / B3 - Code Quality

**Prompt:**

```
Review this code snippet and identify any issues. Be specific about severity:

N+1 query pattern in an endpoint (performance.md violation)
```

retrieval-only: False

**Scores (0-10 each):**

- issue_detection: `score: `
- standards_citation: `score: `
- fix_quality: `score: `
- false_positives: `score: `

**Notes:** 

---

## C / C1 - Planning Coherence

**Prompt:**

```
I need to add a new OAuth provider to the auth module. Produce a step-by-step plan with dependencies, touching
the relevant files in this project. Reference prior decisions where applicable.
```

retrieval-only: False

**Scores (0-10 each):**

- file_accuracy: `score: `
- dependency_ordering: `score: `
- prior_decision_recall: `score: `
- completeness: `score: `

**Notes:** 

---

## C / C2 - Planning Coherence

**Prompt:**

```
I need to refactor the feature-flag system to use typed keys. Produce a step-by-step plan with dependencies, touching
the relevant files in this project. Reference prior decisions where applicable.
```

retrieval-only: False

**Scores (0-10 each):**

- file_accuracy: `score: `
- dependency_ordering: `score: `
- prior_decision_recall: `score: `
- completeness: `score: `

**Notes:** 

---

## C / C3 - Planning Coherence

**Prompt:**

```
I need to migrate from class components to hooks in the dashboard. Produce a step-by-step plan with dependencies, touching
the relevant files in this project. Reference prior decisions where applicable.
```

retrieval-only: False

**Scores (0-10 each):**

- file_accuracy: `score: `
- dependency_ordering: `score: `
- prior_decision_recall: `score: `
- completeness: `score: `

**Notes:** 

---

## D / D1 - Session Continuity

**Prompt:**

```
What were the last three meaningful commits you made in this repo, and what do they accomplish together?
```

retrieval-only: False

**Scores (0-10 each):**

- factual_accuracy: `score: `
- specificity: `score: `
- temporal_ordering: `score: `
- coverage: `score: `

**Notes:** 

---

## D / D2 - Session Continuity

**Prompt:**

```
What was the most recent review-gate finding that resulted in a fix? What was the fix?
```

retrieval-only: False

**Scores (0-10 each):**

- factual_accuracy: `score: `
- specificity: `score: `
- temporal_ordering: `score: `
- coverage: `score: `

**Notes:** 

---

## D / D3 - Session Continuity

**Prompt:**

```
What is the current status of the benchmark harness work? What still needs to happen?
```

retrieval-only: False

**Scores (0-10 each):**

- factual_accuracy: `score: `
- specificity: `score: `
- temporal_ordering: `score: `
- coverage: `score: `

**Notes:** 

---

## D / D4 - Session Continuity

**Prompt:**

```
What decisions have I made about how we handle errors in this codebase?
```

retrieval-only: False

**Scores (0-10 each):**

- factual_accuracy: `score: `
- specificity: `score: `
- temporal_ordering: `score: `
- coverage: `score: `

**Notes:** 

---

## E / E1 - Tool Suggestion Quality

**Prompt:**

```
I need to quickly find all places a specific function is called across the codebase. What's the best approach and what tools should I use?
```

retrieval-only: False

**Scores (0-10 each):**

- tool_relevance: `score: `
- context_awareness: `score: `
- prioritization: `score: `

**Notes:** 

---

## E / E2 - Tool Suggestion Quality

**Prompt:**

```
I need to review a large diff before committing. What's the best approach and what tools should I use?
```

retrieval-only: False

**Scores (0-10 each):**

- tool_relevance: `score: `
- context_awareness: `score: `
- prioritization: `score: `

**Notes:** 

---

## E / E3 - Tool Suggestion Quality

**Prompt:**

```
I need to set up a new project with standard conventions. What's the best approach and what tools should I use?
```

retrieval-only: False

**Scores (0-10 each):**

- tool_relevance: `score: `
- context_awareness: `score: `
- prioritization: `score: `

**Notes:** 

---

## E / E4 - Tool Suggestion Quality

**Prompt:**

```
I need to investigate an intermittent test failure. What's the best approach and what tools should I use?
```

retrieval-only: False

**Scores (0-10 each):**

- tool_relevance: `score: `
- context_awareness: `score: `
- prioritization: `score: `

**Notes:** 

---
