# AI Team Studio Evolution
## Accepted Specification E002
### Canonical Core + E001 Implementation Boundary

Status: ACCEPTED SPECIFICATION
Implementation target: E001
Implementation authority: Human-approved E002
Principle: Observation First. Understanding Together.

---

# 0. Purpose

AI Team Studio is an OS/research environment for arranging multiple intelligences, humans, information, tools, and relationships, then observing what becomes possible through those arrangements.

It is NOT primarily:
- a multi-agent chat application
- a majority-vote system
- an automation product
- an autonomous-agent framework

Automation is a method.
Pattern is an arrangement.
Route is information flow.
Protocol is a rule system governing visibility and action.
Experience is verified knowledge derived from trials.

The system must preserve disagreement, uncertainty, provenance, and raw evidence.

---

# 1. Accepted Core Model

The canonical conceptual loop is:

CASE
→ UNDERSTAND
→ DESIGN SPACE
→ PATTERN / ROUTE
→ PROTOCOL
→ OBSERVATION / ACTION
→ RESULT
→ EVALUATION
→ REFLECTION
→ EXPERIENCE
→ NEXT DESIGN / TRIAL

E001 does NOT implement the whole loop.

E001 implements the minimum research substrate required to run and compare P001/P002 while preserving evidence and information-flow provenance.

---

# 2. E001 Scope

## MUST HAVE

1. Case
2. Minimal Design Space
3. Node / Role / Provider separation
4. P001 Independent / Parallel
5. P002 Sequential
6. Pattern / Route separation
7. Trial as a first-class entity
8. Run + fixed Run Manifest
9. Protocol with structured rules
10. Context Assembly with default-deny behavior
11. Transfer as actual information movement
12. Immutable/write-once Raw Artifact
13. Hash and provenance
14. Manual clipboard/file Provider
15. Observation
16. Synthesis with disagreement preservation
17. Human Gate tied to explicit target
18. Append-only Event Log sufficient to audit important transitions
19. Export / Import
20. Isolation, persistence, schema and replay tests
21. Existing data must never be overwritten during migration or import.

## SHOULD HAVE

1. Materialized current-state snapshot
2. Portable export package
3. Basic deterministic machine Evaluation
4. Process Observer / audit view
5. Legacy migration adapters after baseline audit
6. Linear / Radial views
7. Explicit failure / timeout / unknown status handling

## FUTURE

1. API Providers
2. Proxy automation beyond the minimum manual path
3. Local LLM Provider
4. Action execution
5. Shell / Git / public publishing side effects
6. Automatic Reflection → Experience memory
7. P003 Specialist
8. P004 Debate
9. P005 Evaluator-Optimizer
10. P006/P007 Hybrid patterns
11. Pattern search / optimization
12. Autonomous Trial loops
13. Large-scale Event Sourcing
14. Multi-user authentication / network isolation

---

# 3. Non-Goals of E001

E001 must NOT:
- execute arbitrary shell commands
- modify Git repositories automatically
- expose API credentials to the browser
- automatically publish external content
- claim technical isolation for manual external AI interaction
- treat AI self-report as deterministic verification
- automatically convert Reflection into accepted Experience
- automatically optimize the Studio
- delete or overwrite existing RAW evidence
- silently migrate ambiguous legacy data
- use majority vote as truth

---

# 4. Canonical Entities

## 4.1 Case

Represents the research question/problem.

Minimum:
- caseId
- question
- intent
- constraints
- createdAt
- status

Case status remains coarse. Detailed execution state belongs to Trial / Run / Event.

---

## 4.2 Design Space

Represents the space in which alternative arrangements may be considered.

E001 keeps this minimal.

Minimum:
- designSpaceId
- caseId
- objective / question
- constraints
- notes

Do not implement speculative multi-objective optimization fields in E001.

---

## 4.3 Node

Node answers:

> Who or what participates?

Node is an entity, not a temporary role.

Examples:
- HUMAN
- MODEL
- TOOL
- RESOURCE
- SYSTEM

Node identity must be stable and must not depend only on display name.

---

## 4.4 Role

Role answers:

> What does this participant do in this arrangement?

Examples:
- OBSERVER
- SYNTHESIZER
- GATE
- SOURCE
- EXECUTOR

Role must not be mixed into Node.type.

---

## 4.5 Provider

Provider answers:

> By what execution/transport mechanism is the Node operated?

E001 minimum:
- MANUAL_CLIPBOARD
- MANUAL_FILE

Provider is an adapter boundary.

Future:
- API
- PROXY
- LOCAL_LLM
- CODE_EXECUTOR

---

# 5. Pattern

Pattern is a reusable arrangement/topology.

E001:
- P001 Independent / Parallel
- P002 Sequential

Pattern is not the same as Route.

P001:

Case
 ├→ Observer A
 ├→ Observer B
 ├→ Observer C
 └→ Observer D

Each observer receives only its permitted context.

P002:

Case
 → Observer A
 → Observer B
 → Observer C

Previous output may be intentionally part of the next context.

The old Sequential Studio remains a valuable legacy/reference pattern. It must not be deleted merely because P001 is introduced.

---

# 6. Route

Route is the concrete information-flow graph selected for a Run.

Pattern = arrangement template.
Route = concrete graph used by a specific Run.

Route must use explicit:
- routeId
- version
- fromNodeId
- toNodeId
- allowed artifact kinds / packet semantics
- protocol reference

Do not encode actual historical Transfer solely in Route edges.

---

# 7. Trial

Trial is a first-class research unit.

Trial answers:

> What design/condition are we testing?

Minimum:
- trialId
- caseId
- hypothesis / rationale
- designSpaceId
- patternId
- routeId/version
- protocolId/version
- appliedExperienceIds (optional in E001; normally empty)
- previousTrialId (optional)
- budget
- createdAt
- status

Trial is NOT merely a retry.

A Trial represents an intentional change in experimental conditions.

---

# 8. Run

Run is one execution of a Trial.

Minimum:
- runId
- trialId
- runManifestId
- startedAt
- completedAt
- status

A retry is a new Run, never an overwrite.

---

# 9. Run Manifest

The Run Manifest freezes the conditions under which a Run occurs.

It must record stable references/versions for:
- Case
- Trial
- Pattern
- Route
- Protocol
- Nodes
- Roles
- Providers
- Sources
- initial allowed Artifacts
- relevant configuration

The Manifest itself must be immutable once the Run starts.

A Run must be reconstructable from its Manifest and referenced immutable artifacts.

---

# 10. Source

Source represents external/internal material used by a Run.

Minimum:
- sourceId
- kind
- reference
- claimedHash (optional)
- verifiedHash (optional)
- hashAlgorithm
- access classification
- verification status
- createdAt

Do not silently convert an unverified source into a verified source.

Unknown remains UNKNOWN.

---

# 11. Artifact / RAW

Artifact is the immutable evidence unit.

RAW is never overwritten.

Every received AI response is a new Artifact.

Minimum:
- artifactId
- kind
- content or immutable contentLocation
- encoding
- contentHash
- hashAlgorithm
- createdAt
- source/provenance
- producerNodeId
- parentArtifactIds (optional)
- schemaVersion

Hash semantics must be based on the exact bytes received/stored, with no hidden normalization.

Write-once rule:

If artifactId already exists:
- reject update
- create a new Artifact for a new submission

A correction/revision is a new Artifact linked to its parent.

Observation, Synthesis, Evaluation, and Reflection are derived artifacts/records and must not mutate the RAW Artifact.

E001 must distinguish:
- application-level write-once protection
- operating-system / filesystem tamper resistance

Do not claim the latter unless actually implemented.

---

# 12. Event

E001 does not require full Event Sourcing.

It does require an append-only Event Log sufficient to audit important transitions.

Minimum event concepts:
- CASE_CREATED
- TRIAL_CREATED
- RUN_STARTED
- PACKET_ASSEMBLED
- TRANSFER_CREATED
- ARTIFACT_RECEIVED
- OBSERVATION_CREATED
- SYNTHESIS_CREATED
- EVALUATION_CREATED
- GATE_DECIDED
- RUN_COMPLETED
- RUN_FAILED

Minimum Event fields:
- eventId
- sequence
- timestamp
- actor
- eventType
- targetType
- targetId
- payload
- artifactRefs where applicable

Current state may be represented by a materialized snapshot.

Do not require full event replay architecture in E001.

---

# 13. Transfer

Transfer records what actually moved.

Minimum:
- transferId
- runId
- fromNodeId
- toNodeId
- artifactIds
- packetHash
- method
- createdAt
- status
- isolationAssurance

Route describes intended flow.
Transfer describes actual movement.

For manual operation:
- Studio-generated Packet
- human external paste/action

must be distinguished.

If external action cannot be verified, record UNKNOWN.

---

# 14. Protocol

Protocol is executable/inspectable governance, not prose alone.

Minimum Rule structure:

{
  subject,
  action,
  object,
  effect,
  condition
}

Protocol must define:
- visibility
- allowed artifacts
- prohibited artifacts
- action permissions where relevant
- default-deny behavior
- violation behavior

Rule conflicts must have deterministic resolution.

---

# 15. Context Assembly

Context Assembly is a core E001 boundary.

It constructs the packet given to a Node.

It MUST:
1. start from default deny
2. evaluate Protocol rules
3. include only allowed Artifact IDs
4. produce a deterministic packet representation
5. record the packet hash
6. record the source Artifact IDs
7. reject or pause on policy violation

Independent Observation must be enforced at this layer whenever technically possible.

Prompt text alone is not considered sufficient structural isolation.

---

# 16. Independence Assurance

E001 must not use a single boolean claiming "independent".

Use assurance levels:

- ENFORCED
- PACKET_CONTROLLED
- DECLARED
- UNKNOWN

Meaning:

ENFORCED:
System-level isolation prevents unauthorized context access within the controlled environment.

PACKET_CONTROLLED:
Studio controls and verifies the packet presented to the observer, but cannot control external human/UI behavior.

DECLARED:
Independence is only asserted by declaration/prompt.

UNKNOWN:
Evidence is insufficient.

Manual Provider normally cannot claim full ENFORCED isolation.

The UI and export must show the actual assurance level.

---

# 17. Observation

Observation is the independent result of a Node observing its permitted context.

It must preserve:
- observer Node
- Run
- Transfer/packet reference
- input Artifact references
- raw response Artifact
- structured observation fields where available
- timestamp
- provider

Observation must not silently include peer observations in P001.

---

# 18. Synthesis

Synthesis combines permitted observations.

It must preserve:
- common findings
- differences
- unknowns
- contradictions
- execution needs
- next questions

No majority vote becomes truth by default.

Disagreement is first-class information.

Synthesis must reference its input Observation/Artifact IDs.

---

# 19. Evaluation

E001 implements only basic deterministic evaluation.

L1:
System-verifiable.

Examples:
- schema validity
- hash match
- Artifact existence
- valid reference
- protocol violation
- required field
- state transition

L1 must not depend on an AI self-report.

L3/L4 evaluation may be represented but are not required as autonomous subsystems in the first runnable Slice.

---

# 20. Human Gate

Gate must be tied to a concrete target.

Minimum:
- gateId
- targetType
- targetId
- actionClass
- proposedScope
- status
- decidedBy
- decidedAt
- reason

Statuses:
- APPROVED
- REJECTED
- HOLD
- RETURNED

E001 must not execute consequential external Actions after approval because Action execution is out of scope.

A Gate is an approval record, not an authorization token reusable for unrelated actions.

---

# 21. Reflection / Experience

Reflection and Experience remain conceptually distinct.

E001:
- may create a Reflection/Lesson Candidate record
- does NOT automatically accept Experience

Future flow:

Result
→ Evaluation
→ Reflection
→ Lesson Candidate
→ Verification
→ Accepted Experience

Experience applied to a later Trial must be recorded in that Trial/Run Manifest.

A control Trial without the Experience should remain possible.

---

# 22. Persistence

Canonical E001 persistence is file-backed JSON.

Browser localStorage is NOT the sole source of truth.

Recommended logical separation:

- manifests/
- artifacts/
- events/
- snapshots/
- exports/

The exact physical layout may be adapted after Repository inspection.

Storage requirements:
- write-once Artifact IDs
- append-only Events
- atomic write strategy
- hash verification
- duplicate ID rejection
- corruption detection
- backup/dry-run for migration

A single giant mutable JSON document must not be the canonical immutable store.

---

# 23. Provider Boundary

E001 minimum Provider:
- Manual clipboard/file

Provider contract should define at least:
- send
- collect
- cancel where applicable
- status/health where applicable
- execution/transfer ID
- failure
- timeout
- partial result
- duplicate submission behavior

API/Proxy/Local LLM are future adapters.

---

# 24. Security Boundary

E001 must:
- never place secrets in Case/Artifact/export
- never expose credentials to frontend
- treat external Sources as untrusted data
- enforce Context allowlists
- preserve packet visibility
- prevent destructive Action execution
- preserve existing data on import/migration failure

Future:
- network isolation
- credential vault
- shell sandbox
- advanced prompt-injection defenses
- multi-user authentication

---

# 25. Testing

Required test groups:

## Unit
- schema validation
- ID references
- rule evaluation
- Artifact creation
- hash verification
- state transitions

## Integration
Case → Trial → Run
Packet Assembly → Transfer → Artifact receipt
Artifact → Observation → Synthesis → Gate

## Isolation
Use unique strings such as:
SECRET-A
SECRET-B
SECRET-C

Verify that a P001 observer packet cannot contain peer artifacts.

## Persistence
- duplicate Artifact ID rejected
- resubmission creates new Artifact
- hash mismatch detected
- corruption does not overwrite valid data
- unknown schema version rejected safely

## Replay
Same Run Manifest + same input Artifact versions produce the same packet representation/hash.

## Import/Export
- reference integrity
- secret exclusion
- unsupported version rejection
- old data unchanged

## Gate
- target mismatch rejected
- approval cannot be reused for unrelated target
- no consequential execution without required approval

---

# 26. Migration

Do not migrate legacy data until each legacy format receives a baseline audit.

Existing:
- Lite / Sequential Studio
- Operational 001
- Integrated 002
- other unmanaged/export artifacts

must not be overwritten.

Migration must be copy-on-write.

Unknown legacy information remains UNKNOWN.

No inferred Provider/Run/Transfer/Independence information may be fabricated.

Legacy Sequential behavior remains available as a valid Pattern/reference.

---

# 27. Rollback

E001 must be isolated from existing stores.

Migration:
- dry-run
- backup
- copy-on-write
- validation before commit

On:
- schema mismatch
- hash mismatch
- import failure
- corruption

stop writing and preserve the old data.

Rollback does not delete events/artifacts. A new Run records recovery/failure.

---

# 28. E001 Runnable Slice

The first runnable Slice is intentionally small:

1. Create Case
2. Create Trial
3. Select P001 or P002
4. Freeze Run Manifest
5. Assemble Protocol-controlled Context
6. Generate Manual Packet
7. Record Transfer
8. Receive RAW response as immutable Artifact
9. Record Observation
10. Create basic Synthesis
11. Preserve disagreement/unknowns
12. Record Human Gate
13. Export/import
14. Run automated schema/isolation/persistence tests

This Slice must work before additional automation is added.

---

# 29. Architecture

Recommended shape:

UI / Views
    ↓
Application Use Cases
    ↓
Canonical Domain Contracts
    ↓
Storage / Provider / Import-Export Adapters

Use a small modular monolith.

Do not prematurely introduce microservices, a complex database, or autonomous-agent infrastructure.

---

# 30. Suggested Repository Structure

This is an architectural target, not an assumption about the current repository.

src/
  domain/
    case/
    design-space/
    node-role-provider/
    pattern-route/
    protocol/
    trial-run/
    artifact/
    transfer/
    gate/
  application/
    create-case/
    create-trial/
    create-run/
    assemble-context/
    record-transfer/
    receive-artifact/
    synthesize/
    evaluate-gate/
  infrastructure/
    storage/
      json-file-store/
      integrity/
    providers/
      manual/
    import-export/
  ui/
    case-studio/
    linear-view/
    radial-view/
  tests/
    unit/
    integration/
    isolation/
    persistence/
    migration/
    fixtures/

Do NOT restructure the actual repository until a baseline audit confirms the current structure.

---

# 31. Acceptance Criteria

E001 is accepted only if:

AT-001:
Same Case can run as P001 and P002 using the same Core.

AT-002:
P001 packets contain only permitted Case/Source/context artifacts and no peer Observation artifacts.

AT-003:
Packet contents and source Artifact IDs are hashable and auditable.

AT-004:
Manual independence is displayed with an honest assurance level.

AT-005:
Every RAW response is a separate immutable Artifact.

AT-006:
Resubmission never changes a previous RAW Artifact.

AT-007:
Route and actual Transfer are distinguishable.

AT-008:
Synthesis references its input Observations and preserves contradictions/differences.

AT-009:
Protocol violations can be detected and block/pause according to policy.

AT-010:
Human Gate has a concrete target and cannot be reused for another target.

AT-011:
Run Manifest fixes the versions/IDs necessary to reconstruct the Run.

AT-012:
Events preserve important execution history without requiring full Event Sourcing.

AT-013:
Schema, persistence, isolation, import/export and replay tests are runnable.

AT-014:
Unsupported schema versions do not overwrite existing data.

AT-015:
Migration does not overwrite source data and preserves UNKNOWN where provenance is insufficient.

---

# 32. Implementation Constraints

Claude Code MUST:

- inspect the actual repository before modifying anything
- identify the exact target branch
- preserve main
- preserve feature/integrated-002
- create/use a new implementation branch as appropriate
- not overwrite existing assets
- not delete legacy Studio implementations
- not expose API keys
- not assume an API is available
- not introduce a build system unless required by the existing repository
- prefer small incremental commits
- provide a commit name before commit
- stop before destructive or irreversible operations
- provide tests for every new core guarantee
- distinguish observed repository facts from proposed architecture
- ask for Human Gate approval before irreversible migration or merge

Raw URLs, external documentation, or AI-generated claims are not automatically source-of-truth.

---

# 33. Implementation Order

Phase E001-A:
Baseline audit and branch preparation.

Phase E001-B:
Canonical contracts + revised Schema.

Phase E001-C:
Artifact storage + hashing + write-once behavior.

Phase E001-D:
Case / Trial / Run / Manifest.

Phase E001-E:
Pattern P001/P002 + Route.

Phase E001-F:
Protocol + Context Assembly + isolation tests.

Phase E001-G:
Manual Provider + Transfer.

Phase E001-H:
Observation + Synthesis + Gate.

Phase E001-I:
Event Log + snapshot + Export/Import.

Phase E001-J:
Acceptance tests + documentation + Human Gate review.

Do not start later phases if the previous phase's tests fail.

---

# 34. Definition of Done

E001 is NOT done because the UI appears to work.

E001 is done when:

1. The canonical Schema validates real records.
2. RAW cannot be overwritten through the supported application path.
3. P001 and P002 are represented by the same Core.
4. Protocol controls Context Assembly.
5. P001 isolation is machine-tested within Studio boundaries.
6. Actual Transfers are recorded separately from planned Routes.
7. Trial and Run are reproducible from frozen references.
8. Synthesis preserves disagreement.
9. Human Gate targets are explicit.
10. Existing data remains untouched.
11. Acceptance tests pass.
12. The implementation report identifies remaining UNKNOWNs and limitations honestly.

---

# 35. Explicitly Deferred

Do not implement merely because these concepts appear in the broader Evolution philosophy:

- autonomous agents
- autonomous Trial loops
- automatic Experience memory
- API orchestration
- Local LLM integration
- shell execution
- Git automation
- public publishing
- Debate pattern
- Specialist pattern
- Evaluator-Optimizer
- large-scale Event Sourcing
- multi-user security
- optimization/search of Patterns

These are later experiments, not E001 requirements.

---

# 36. Final Architectural Principle

AI Team Studio must not claim a property merely because the UI or prompt says that the property exists.

If the system says:

INDEPENDENT
IMMUTABLE
VERIFIED
APPROVED
TRANSFERRED
REPRODUCIBLE

there must be a defined evidence boundary for that claim.

When a guarantee is impossible, the system records the limitation.

Observation First.
Understanding Together.
