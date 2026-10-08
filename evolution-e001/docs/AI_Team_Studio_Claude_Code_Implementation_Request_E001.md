# Claude Code Implementation Request
## AI Team Studio Evolution — E001
### Implement ONLY the Accepted Specification E002

You are now receiving the Human-approved Accepted Specification E002.

This is an implementation request, not another architecture review.

The attached/preceding E002 is the authoritative design boundary for this implementation.

## 1. First Rule: Inspect Before Modifying

Before changing anything:

1. Inspect the actual repository.
2. Identify repository root.
3. Identify current branch.
4. Identify main branch and feature/integrated-002 branch.
5. Inspect working-tree status.
6. Identify uncommitted changes.
7. Identify untracked files.
8. Identify existing Lite / Sequential Studio, Operational Studio, Integrated 002, and related assets.
9. Identify the actual current Schema and data storage.
10. Compare the real repository state with E002.

Do NOT assume that the architectural target directory structure already exists.

Do NOT overwrite or delete existing assets.

## 2. Branch Rule

Do not modify main.

Do not modify feature/integrated-002.

Create or use a dedicated E001 implementation branch after reporting the current repository state.

Before any destructive or irreversible operation, stop and request Human Gate approval.

## 3. Source-of-Truth Rule

E002 is the accepted implementation specification.

However, repository facts must come from the actual repository.

If the repository differs from the expected state:
- report the difference
- do not guess
- do not silently overwrite
- propose the smallest safe adaptation

External URLs, old AI reports, or generated assumptions are not automatically source-of-truth.

## 4. Implementation Goal

Implement the smallest runnable E001 Slice:

Case
→ Trial
→ Run Manifest
→ P001/P002
→ Protocol-controlled Context Assembly
→ Manual Packet
→ Transfer
→ immutable RAW Artifact
→ Observation
→ Synthesis
→ Human Gate
→ Export/Import
→ automated tests

Do not expand E001 into autonomous-agent infrastructure.

## 5. Mandatory Guarantees

The implementation must make the following real system properties testable:

### RAW
- write-once Artifact
- unique Artifact ID
- exact content hash
- no overwrite through supported application path
- resubmission creates a new Artifact

### Independent Observation
- P001 uses default-deny Context Assembly
- peer Observation artifacts are not included
- packet source Artifact IDs are recorded
- packet hash is recorded
- manual execution does NOT claim full technical isolation

### Protocol
- structured rules
- subject/action/object/effect/condition
- deterministic rule evaluation
- violation can block/pause

### Transfer
- actual transfer is recorded separately from Route
- source node
- destination node
- Artifact IDs
- packet hash
- method
- status
- assurance

### Trial / Run
- Trial is first-class
- Run is one execution
- Run Manifest freezes required IDs/versions
- retry is a new Run

### Human Gate
- explicit target
- decision
- scope
- actor
- timestamp
- reason
- approval cannot be reused for unrelated targets

## 6. Persistence

Use file-backed JSON as the canonical E001 persistence direction.

Do not make localStorage the sole source of truth.

Use an implementation appropriate to the actual repository.

Prefer logical separation of:
- manifests
- artifacts
- events
- snapshots
- exports

Do not create an unnecessarily complex database.

Do not implement full Event Sourcing.

Use an append-only Event Log plus a readable/materialized state snapshot.

## 7. Provider

E001 minimum Provider:

MANUAL_CLIPBOARD
MANUAL_FILE

Do not implement API, Proxy, Local LLM, shell execution, Git execution, or external publishing unless a repository constraint makes a minimal compatibility layer unavoidable.

If an existing adapter already exists, preserve it, but do not falsely mark it verified.

## 8. Testing Is Part of Implementation

Do not consider the feature complete when the UI works.

Implement tests for:

- Schema validation
- Artifact hash
- duplicate Artifact ID rejection
- resubmission creates new Artifact
- P001 isolation
- P002 sequential flow
- Protocol violation
- Transfer recording
- Run Manifest
- Gate target binding
- Import/export
- unsupported schema version
- corruption/hash mismatch
- replay/packet reconstruction where feasible

Use unique test values such as:

SECRET-A
SECRET-B
SECRET-C

to prove that peer context does not leak into P001 packets.

## 9. Migration

Do NOT migrate existing legacy data immediately.

First inspect and report the real formats.

If migration is implemented:
- copy-on-write only
- source remains untouched
- preserve original IDs/references
- preserve UNKNOWN when provenance is insufficient
- dry-run before write
- no inferred data

Legacy Sequential Studio remains available.

Do not delete it.

## 10. Implementation Order

Follow this sequence unless the repository requires a safer equivalent:

E001-A
Baseline audit / branch preparation

E001-B
Canonical contracts + Schema revision

E001-C
Artifact storage + hash + write-once

E001-D
Case / Trial / Run / Manifest

E001-E
P001/P002 + Route

E001-F
Protocol + Context Assembly + isolation tests

E001-G
Manual Provider + Transfer

E001-H
Observation + Synthesis + Gate

E001-I
Event Log + snapshot + Export/Import

E001-J
Acceptance tests + documentation

After each phase:
- run relevant tests
- report result
- do not continue if a core guarantee is broken

## 11. Commit Discipline

Before every commit, report:

- proposed commit name
- files changed
- tests run
- result
- remaining risks

Do not commit automatically if the change is destructive, ambiguous, or outside E002.

Use small commits.

## 12. Stop Conditions

STOP and ask for Human Gate if:

- current repository state conflicts materially with E002
- a destructive migration is required
- existing data would be overwritten
- credentials/API keys would need to be exposed
- a requested guarantee cannot actually be enforced
- the implementation would require changing main
- a legacy asset must be deleted
- the E002 scope would need to expand substantially

## 13. Required Implementation Report

At the end, report:

1. Actual repository baseline
2. Branch used
3. Files created
4. Files modified
5. Files intentionally preserved
6. Schema changes
7. Persistence implementation
8. Artifact immutability implementation
9. Protocol enforcement
10. Context isolation
11. Transfer implementation
12. Trial/Run implementation
13. Human Gate implementation
14. Tests
15. Test results
16. Known limitations
17. UNKNOWNs
18. Remaining E001 work
19. Recommended next step
20. Proposed final commit name

Do not claim a guarantee that the tests or implementation do not support.

## 14. Most Important Rule

The goal is not to make the system look more sophisticated.

The goal is to make the core research properties real, inspectable, reproducible, and testable.

If a feature cannot yet be guaranteed, record that limitation rather than simulating the guarantee in the UI.

Observation First.
Understanding Together.
