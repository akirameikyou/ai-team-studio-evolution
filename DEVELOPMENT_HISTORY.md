# AI Team Studio Evolution — Development History

## Purpose

This project explores AI Team Studio as a **Recognition Space**, not merely an answer-collection interface.

The central principle is:

> **Observation First. Understanding Together.**

The development history is preserved as part of the research object. Each stage records how the next recognition space emerged from the previous observation.

---

# 000 — Origin

The project originated from the AI Team Studio v2 collaboration model in which GPT, Claude, Gemini, and Codex observe the same work from different responsibilities.

The initial concern was not simply whether an implementation was correct, but why two capable observers could reach different judgments from apparently related evidence.

This led to the question:

> **Can the conditions under which an AI recognizes something be made observable?**

---

# 001 — WU-1A

Theme: **Save Safety / Active State Protection**

WU-1A was implemented and tested with Claude reporting PASS-C while Codex maintained HOLD.

The difference did not immediately establish that one observer was right and the other wrong. It exposed a need to inspect the evidence, execution conditions, requirements mapping, and observation state behind the judgments.

The WU-1A implementation itself is not reproduced here; it is the historical origin of the Recognition Space hypothesis.

---

# 002 — Fact Check

A fact-check phase examined the evidence behind the differing judgments.

Claude's local session record contained detailed browser operations, server commands, T1–T23 test records, UI/state observations, console information, and Git state.

Codex subsequently confirmed that the evidence record existed, but maintained HOLD because the available evidence had not yet been independently mapped across the full formal requirement set and runtime reproduction had not been performed.

The important observation was:

> **The evidence can exist while its accessibility and interpretation differ between observers.**

---

# 003 — Recognition Space Hypothesis

The project shifted from asking:

> “Which AI is correct?”

toward:

> **“What recognition space caused each AI to reach its judgment?”**

The initial hypothesis was that SOURCE, CONTEXT, STATE, execution possibility, evidence, and observer position can affect what an AI is able to recognize.

This introduced the conceptual loop:

```text
SPACE → OBSERVE → DIFFERENCE → SYNTHESIS → FIX → NEXT SPACE
```

---

# 004 — Difference Hypothesis

Difference was reframed.

It should not automatically be treated as an error to eliminate.

Instead:

> **Meaningful differences between observers are observation resources.**

A difference can reveal an unknown condition, an evidence boundary, a context difference, or a difference in reasoning.

The question therefore becomes:

> **Where did the difference originate?**

---

# 005 — Evolution Hypothesis

The project then proposed that judgment should not simply terminate the process.

A fixed judgment can become material for the next recognition space.

Therefore:

```text
Observation
    ↓
Difference
    ↓
Synthesis
    ↓
Human Fix
    ↓
Next Space
    ↓
New Observation
```

The goal is not autonomous AI consensus.

The goal is an observable evolution of recognition conditions.

---

# 006 — Prototype 001

Prototype 001 was created as a deliberately small, static implementation.

Its purpose was to make the Recognition Space physically observable before attempting automation.

The prototype contains:

- SPACE / SOURCE
- CONTEXT
- STATE
- OBSERVATION PROTOCOL
- JUDGMENT
- REASON
- EVIDENCE
- UNKNOWN
- four independent observer inputs
- DIFFERENCE
- NEXT SPACE
- HUMAN FIX
- DEVELOPMENT HISTORY
- LOCAL RECORD

It intentionally does not call AI APIs.

Independent observations are entered externally so that the observation process remains explicit.

**Implementation status:** Prototype 001 fixed and published.

---

# 007 — Five-Observer Independent Observation

On 2026-10-07, Prototype 001 was independently observed by:

- Claude
- Gemini
- Codex
- Meta AI / Muse Spark
- GPT (independent report from a separate chat)

The observers were instructed not to read one another's reports.

All five returned **HOLD**, but their reasons and proposed next spaces differed.

This was treated as an observation result rather than a failure.

### Claude observed

The importance of **observation conditions themselves**.

### Gemini observed

The importance of **transition, synthesis, and inheritance between recognition spaces**.

### Codex observed

The possibility of experimentally separating **SOURCE and CONTEXT**.

### Meta AI observed

The importance of **observability and evidence accessibility** as data.

### GPT observed

The need to identify the **origin layer of Difference**.

---

# 008 — SYNTHESIS

The five independent observations were synthesized without averaging them into a single score.

A common direction emerged:

> **The next question is not simply whether observers disagree, but what conditions cause their recognitions to diverge.**

The Recognition Space hypothesis was therefore refined from:

> “A space for comparing AI observations.”

toward:

> **“A space for observing the conditions under which different intelligences recognize the same object differently, and preserving those differences as material for the next recognition space.”**

The resulting next-space candidate is:

# Difference Origin Space

The proposed experiment is to observe whether differences originate in:

- SOURCE
- CONTEXT
- STATE
- OBSERVER
- EVIDENCE
- REASONING
- JUDGMENT

A particularly concrete experiment is to keep SOURCE fixed while varying CONTEXT.

Observation conditions themselves may also need to become first-class data, including access route, available evidence, runtime environment, prior knowledge, observer role, observation time, and observable range.

**Synthesis status:** fixed as the current development hypothesis.  
**Prototype 002:** not yet implemented.

For the complete synthesis record, see [`SYNTHESIS_REPORT.md`](SYNTHESIS_REPORT.md).

---

# Current State

```text
WU-1A
  ↓
Fact Check
  ↓
Recognition Space Hypothesis
  ↓
Difference Hypothesis
  ↓
Evolution Hypothesis
  ↓
Prototype 001
  ↓
Independent Observation
  ↓
Five Observer Reports
  ↓
SYNTHESIS
  ↓
Difference Origin Space
  ↓
Prototype 002 — future experiment
```

Prototype 001 remains preserved as the first fixed Recognition Space artifact.

The next implementation should be based on the observed differences above, not on assumptions made before the independent observation.

---

# 009 — Claude / Codex Independent Recheck

### Purpose

After Prototype 001 v1.1 was reflected to GitHub, Claude and Codex independently checked the same artifact. This entry records where their observations, evidence, and judgments matched and where they diverged.

### Observation

For the same v1.1, the two observers broadly agreed on the main integrity facts: the Prototype body (`index.html`) is unchanged, the Git change is limited to documentation, the target commit `a428a2a` is on GitHub `main`, and the GitHub Pages URL responds with HTTP 200.

### Difference

The final judgments diverged:

- Claude: **HOLD**
- Codex: **PASS** (for a limited scope)

### Difference Origin Candidate

The difference may have arisen from differences in Evidence threshold, Judgment boundary, Scope, or Action policy.

The cause is not determined.

### Important Observation

For the same artifact, broad agreement on the facts did not produce agreement on the judgment.

### UNKNOWN

The browser console of the published page was not checked by either observer. Claude's in-app browser opened the published URL but was refused when reading the page content and console.

### Relation to Next Space

This observation may be a concrete instance of the "Difference Origin Space" fixed as a candidate in the Prototype 001 SYNTHESIS.

Prototype 002 is not implemented by this record.

For the detailed record, see [`INDEPENDENT_RECHECK_001.md`](INDEPENDENT_RECHECK_001.md).
