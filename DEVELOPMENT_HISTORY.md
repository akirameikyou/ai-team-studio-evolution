# AI Team Studio Evolution — Prototype 001

## Purpose

This prototype tests a hypothesis that AI Team Studio should be designed as a **recognition space**, not merely as an answer-collection interface.

Core loop:

**SPACE → OBSERVE → DIFFERENCE → SYNTHESIS → FIX → NEXT SPACE**

The prototype deliberately does not call AI APIs. The four observers remain independent, and their observations are entered or imported explicitly.

## Development history

### 001 — WU-1A
Claude reported PASS-C. Codex reported HOLD.

### 002 — Fact Check
The Claude local JSONL session record was confirmed to contain browser interaction, server command, local state/UI evidence, T1–T23 test records, console observations, and Git state. Codex confirmed the existence of this evidence but retained HOLD because it did not independently reproduce the browser run and did not complete a full formal requirement-to-assertion mapping.

### 003 — Recognition Space Hypothesis
The important observation was not simply PASS versus HOLD. The same underlying target can yield different judgments when evidence, context, execution state, or observable scope differs.

### 004 — Difference Hypothesis
Differences between observers are treated as an observation resource rather than automatically as errors.

### 005 — Evolution Hypothesis
A fixed human decision becomes material for the next recognition space instead of being treated as the end of the process.

### 006 — Prototype 001
A minimal static prototype was created with:

- SOURCE
- CONTEXT
- STATE
- JUDGMENT
- REASON
- EVIDENCE
- UNKNOWN
- four independent observer fields
- DIFFERENCE
- NEXT SPACE
- HUMAN FIX
- local save/load
- JSON export

## What this prototype is intended to test

1. Can four AIs independently observe the same prototype without being forced into one answer?
2. Can the differences between their observations be preserved explicitly?
3. Does the distinction between judgment, evidence, unknowns, and observation conditions improve auditability?
4. Does a human FIX provide a meaningful transition into the next recognition space?
5. What should Prototype 002 contain after the four independent observations?

## Non-goals

- This is not the replacement for AI Team Studio v2.
- This is not an AI orchestration engine.
- This prototype does not claim that the "recognition space" analogy is a physical or quantum-mechanical claim.
- No external factual source is silently treated as source-of-truth.

## First experiment

Show the same repository and the same prototype to GPT, Claude, Gemini, and Codex independently.

Ask each to report:

- What can you observe?
- What can you not observe?
- What is unclear?
- What is missing?
- What should be changed?
- What should be preserved?
- What is your strongest recommendation for Prototype 002?

Do not show one observer's report to another before their independent report is complete.
