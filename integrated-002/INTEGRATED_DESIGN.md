# AI Team Studio Integrated 002 — Design

## Purpose
Integrated 001 was a filled design-validation model. Integrated 002 is the practical empty-case prototype.

## Seven-stage workflow
1. CASE — human places a simple question.
2. SOURCE — identify the object and observation conditions.
3. OBSERVE / LENS — assign independent observer tasks.
4. DIFFERENCE — preserve divergence and its possible origin.
5. SYNTHESIS — produce the information needed for the next execution.
6. HUMAN GATE — APPROVE / HOLD / REVISE.
7. NEXT — produce the next action and next observation condition.

## Operational shift
Old Studio:
human question -> GPT structures -> human copy/paste -> other AIs -> GPT aggregates.

Integrated 002:
human question -> CASE/PACKET -> AI tasks -> results return to Case -> synthesis -> execution task.

The prototype does not yet call external AI APIs. It establishes the shared work state and packet format first.

## Visual direction
No Hokusai imagery, Fuji, washi texture, or decorative artwork.
The visual language is based on the strongest readability traits observed in the Codex and Meta submissions:
- light background
- Japanese-first labels
- flat cards
- restrained borders
- clear hierarchy
- seven-stage progress
- low visual noise

## Automation boundary
Automate transport, storage, formatting, comparison, and packet generation.
Keep meaning, adoption, HOLD/REVISE, and consequential implementation decisions explicit.

## First real-use test
Use a real Case rather than pre-filling conclusions. The first test should measure:
- time to understand the workflow
- number of manual copy/paste operations
- whether all AIs can access the same Case/SOURCE
- whether Observer Conditions survive transport
- whether synthesis produces an actionable execution task
- whether Claude/Codex can proceed from the resulting task without reconstructing context
