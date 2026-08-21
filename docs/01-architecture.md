# 01 — Architecture

## The one design principle: fat engine, thin skill

Multi-agent pipelines fail when too much logic lives in prose (skills) that a
model re-interprets every run. This template pushes everything **deterministic**
into Python and leaves the skill only what needs **judgment**.

```
                 ┌─────────────────────────────────────────────┐
                 │  triage.yaml  (your domain, as data)         │
                 └───────────────────────┬─────────────────────┘
                                         │ loaded + validated by
                                         ▼
   ┌──────────────────────────  engine/  (generic, testable) ───────────────────┐
   │ config.py    TriageConfig — typed view + validation                         │
   │ engine.py    TriageEngine — dedup · score · route · generate task specs     │
   │ scoring.py   apply rubric (LLM-breakdown mode + deterministic mode)         │
   │ routing.py   classification → path                                          │
   │ dedup.py     similarity (token-cosine; embedding backend is not wired)       │
   │ item_vault   one md file per item   kanban_store  writes the board          │
   └───────────────────────────────┬────────────────────────────────────────────┘
                                    │ called by
            ┌───────────────────────┴───────────────────────┐
            ▼                                                 ▼
  triage-orchestrator SKILL.md                      proposal_actions.py
  (judgment only: score dims,                       (gate handler: reads the
   classify, write proposal prose;                   post-gate chain from config,
   calls engine for everything else)                 spawns it on approve)
```

## Why this split

- **Testable.** `engine/` runs without a model or a live board. `tests/` exercise
  scoring, routing, dedup, and chain construction against a synthetic config. A
  prose-only pipeline can't be unit-tested.
- **Adaptable.** Repointing at a new domain is editing data (`triage.yaml`), not
  rewriting program logic.
- **Honest boundaries.** The model does the fuzzy parts (is this painful? is the
  existing solution confusing? what's a good proposal?). Code does the crisp parts
  (sum the score, compare to threshold, look up the route, build the cards).

## What the engine does NOT do

The engine returns **task specs** (plain dataclasses: title/body/role/parents/
workspace) rather than touching the board itself. `proposal_actions.py` applies
and links post-gate fulfillment specs through `KanbanStore`; the orchestrator
skill is currently responsible for applying the pre-gate specs and constructing
the route fan-in card. This keeps calculations pure, but the pre-gate DAG is not
yet fully enforced by deterministic code.

## Data flow of one item

1. A **scout** writes a report and creates an `intake` card (it only detects).
2. The **orchestrator skill is intended to** parse it, ask the engine for dedup
   matches, score it
   (judgment, validated by the engine), and — if it clears the bar — creates a
   triage card plus the **research fan-out** (`engine.research_specs`).
3. `research_specs()` returns evidence specs parented to a supplied root id;
   `classifier_spec()` returns their downstream fan-in. The
   intake orchestrator creates that triage root parented to its current intake
   card, records its id in the item, then creates a **route** card parented to
   the classifier. The parent edge
   keeps the triage root undispatchable until the full graph exists. After the
   intake completes, the triage worker only verifies the graph and completes
   itself to release the lanes. This release step is model-applied because
   Hermes workers may only complete their own task id; the engine creates
   neither the route card nor the board transitions today.
4. The orchestrator reads the classifier's output and calls `engine.route()` to
   pick a path. `pre_gate_actions.py` applies the ordered `engine.prep_specs()`
   through resolved profiles and creates the linked **prep** chain idempotently.
5. It drafts a proposal and **sends it to the human** (`hermes send`).
6. The human replies; the orchestrator shells to `proposal_actions.py`, which
   reads `paths.<path>.fulfill` and spawns the **fulfillment** chain in a shared
   persistent workspace (`engine.fulfillment_specs`).
7. Delivery remains an orchestrator instruction after the final task; this repo
   has no deterministic completion hook that sends it automatically.

## Multiple pipelines, one authenticated fleet

Separate boards and workspace roots isolate task state and artifacts. A stable
`pipeline_id` namespaces rendered skills, cron jobs, retry keys, and gate
references. Abstract roles may map to existing profiles marked `shared: true`
or to pipeline-specific profiles, so one pipeline can mix both.

Exactly one gateway owns Kanban dispatch. Hermes sweeps every board and pins a
spawned worker to its board, so another pipeline adds a board rather than a
dispatcher. Rendered skills pass their exact config path to deterministic
adapters; ambient `TRIAGE_CONFIG` remains a CLI convenience.

This follows the official Hermes contracts for [multi-board Kanban](https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban),
[profiles](https://hermes-agent.nousresearch.com/docs/user-guide/profiles), and
[profile-local cron](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron).

## Where to extend (mechanism, not topic)

- New scoring backend → add a mode in `scoring.py`, expose via `engine.py`.
- Embedding dedup → populate item `embedding`, swap the cosine source in
  `dedup.py` (contract unchanged).
- New step type → add a method on `TriageEngine` returning `TaskSpec`s.

Adding your *subject matter* is never an engine change — it's `triage.yaml`.
