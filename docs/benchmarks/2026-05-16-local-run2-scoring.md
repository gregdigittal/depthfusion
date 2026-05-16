# CIQS Scoring Template - local / run 2

> Generated: 2026-05-16
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

1. `session` / `2026-05-16-depthfusion-session` (score=24.2888)
   > # Session: 2026-05-16
**Date:** 2026-05-16
**Started:** 07:13
**Last Updated:** 10:55
**Project:** depthfusion
**Branch:** main
**Worktree:** /home/gregmorris/projects/depthfusion

---


--- SESSION END at 07:14:20 ---
Project: depthfusion

2. `session` / `2026-05-15-depthfusion-session` (score=15.905)
   > --- COMPACTION EVENT at 08:12:16 ---
Project: depthfusion
Directory: /home/gregmorris/projects/depthfusion
Trigger: context limit reached
Hook Input: {"session_id":"cb5c5db4-ceb5-4134-b8e7-1808787af076","transcript_path":"/home/gregmorris/.
3. `session` / `2026-05-15-agent-ops-session` (score=14.7754)
   > --- SESSION END at 03:40:58 ---
Project: agent-ops
Directory: /home/gregmorris/projects/agent-ops
End Reason: {"session_id":"b08f01a8-fbe1-4319-a143-3085c16dcb8d","transcript_path":"/home/gregmorris/.claude-acc2/projects/-home-gregmorris-pr
4. `session` / `2026-05-15-skillforge-session` (score=14.2066)
   > --- SESSION END at 03:34:43 ---
Project: skillforge
Directory: /home/gregmorris/projects/skillforge
End Reason: {"session_id":"097ee9a5-0a7b-4aaf-99c4-fb37a9c5b437","transcript_path":"/home/gregmorris/.claude-acc1/projects/-home-gregmorris-
5. `session` / `2026-05-15-digittal-ccrs-session` (score=13.3495)
   > --- SESSION END at 03:35:03 ---
Project: digittal-ccrs
Directory: /home/gregmorris/projects/agreement-automation
End Reason: {"session_id":"2079fc01-a61c-444f-bfb7-ec497f97a130","transcript_path":"/home/gregmorris/.claude-acc1/projects/-hom

**Scores (0-10 each):**

- relevance: `score: 2`
- specificity: `score: 2`
- confidence_calibration: `score: 1`
- novel_signal: `score: 1`

**Notes:** Only 1/5 blocks from target project (skillforge). That block covers SkillChain/UI work, not router error handling. Depthfusion sessions rank highest due to recency bias. Cross-project contamination is the primary issue here.

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

1. `session` / `2026-05-15-skillforge-session` (score=21.225)
   > --- SESSION END at 03:34:43 ---
Project: skillforge
Directory: /home/gregmorris/projects/skillforge
End Reason: {"session_id":"097ee9a5-0a7b-4aaf-99c4-fb37a9c5b437","transcript_path":"/home/gregmorris/.claude-acc1/projects/-home-gregmorris-
2. `session` / `2026-05-16-depthfusion-session` (score=20.3675)
   > # Session: 2026-05-16
**Date:** 2026-05-16
**Started:** 07:13
**Last Updated:** 10:55
**Project:** depthfusion
**Branch:** main
**Worktree:** /home/gregmorris/projects/depthfusion

---


--- SESSION END at 07:14:20 ---
Project: depthfusion

3. `session` / `2026-05-15-agent-ops-session` (score=20.1791)
   > --- SESSION END at 03:40:58 ---
Project: agent-ops
Directory: /home/gregmorris/projects/agent-ops
End Reason: {"session_id":"b08f01a8-fbe1-4319-a143-3085c16dcb8d","transcript_path":"/home/gregmorris/.claude-acc2/projects/-home-gregmorris-pr
4. `session` / `2026-05-15-depthfusion-session` (score=17.8914)
   > --- COMPACTION EVENT at 08:12:16 ---
Project: depthfusion
Directory: /home/gregmorris/projects/depthfusion
Trigger: context limit reached
Hook Input: {"session_id":"cb5c5db4-ceb5-4134-b8e7-1808787af076","transcript_path":"/home/gregmorris/.
5. `session` / `2026-05-16-skillforge-session` (score=16.1589)
   > --- SESSION END at 09:54:40 ---
Project: skillforge
Directory: /home/gregmorris/projects/skillforge
End Reason: {"session_id":"097ee9a5-0a7b-4aaf-99c4-fb37a9c5b437","transcript_path":"/home/gregmorris/.claude-acc1/projects/-home-gregmorris-

**Scores (0-10 each):**

- relevance: `score: 4`
- specificity: `score: 4`
- confidence_calibration: `score: 1`
- novel_signal: `score: 3`

**Notes:** 2/5 blocks from skillforge project. Block 1 (rank 1) is a SkillForge session that mentions YAML serializer/deserializer and SkillChain model — adjacent to Skill IR. Block 5 (rank 5) is also skillforge. Useful signal buried among off-topic blocks.

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

1. `session` / `2026-05-16-depthfusion-session` (score=22.0564)
   > # Session: 2026-05-16
**Date:** 2026-05-16
**Started:** 07:13
**Last Updated:** 10:55
**Project:** depthfusion
**Branch:** main
**Worktree:** /home/gregmorris/projects/depthfusion

---


--- SESSION END at 07:14:20 ---
Project: depthfusion

2. `session` / `2026-05-14-digittal-ccrs-session` (score=17.8621)
   > --- SESSION START at 02:44:49 ---
Project: digittal-ccrs
Directory: /home/gregmorris/projects/agreement-automation
--- SESSION START at 02:44:49 ---
Project: digittal-ccrs
Directory: /home/gregmorris/projects/agreement-automation
--- SESSIO
3. `session` / `2026-05-15-digittal-ccrs-session` (score=17.1209)
   > --- SESSION END at 03:35:03 ---
Project: digittal-ccrs
Directory: /home/gregmorris/projects/agreement-automation
End Reason: {"session_id":"2079fc01-a61c-444f-bfb7-ec497f97a130","transcript_path":"/home/gregmorris/.claude-acc1/projects/-hom
4. `session` / `2026-05-15-depthfusion-session` (score=15.8927)
   > --- COMPACTION EVENT at 08:12:16 ---
Project: depthfusion
Directory: /home/gregmorris/projects/depthfusion
Trigger: context limit reached
Hook Input: {"session_id":"cb5c5db4-ceb5-4134-b8e7-1808787af076","transcript_path":"/home/gregmorris/.
5. `session` / `2026-05-15-agent-ops-session` (score=14.2064)
   > --- SESSION END at 03:40:58 ---
Project: agent-ops
Directory: /home/gregmorris/projects/agent-ops
End Reason: {"session_id":"b08f01a8-fbe1-4319-a143-3085c16dcb8d","transcript_path":"/home/gregmorris/.claude-acc2/projects/-home-gregmorris-pr

**Scores (0-10 each):**

- relevance: `score: 1`
- specificity: `score: 1`
- confidence_calibration: `score: 1`
- novel_signal: `score: 1`

**Notes:** None of the 5 retrieved blocks contain explicit commit message style preferences. The actual preference rules are in git-workflow.md (not in the session knowledge base). Retrieval returns generic session event logs for this cross-project preference query.

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
- specificity: `score: 1`
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
- specificity: `score: 1`
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
- specificity: `score: 1`
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
- specificity: `score: 1`
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
