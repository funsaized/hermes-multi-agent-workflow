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
   │ config.py    TriageConfig — typed view + validation (incl. model routing)   │
   │ engine.py    TriageEngine — dedup · score · route · generate task specs     │
   │ scoring.py   apply rubric (LLM-breakdown mode + deterministic mode)         │
   │ routing.py   classification → path                                          │
   │ dedup.py     similarity (token-cosine; embedding backend is not wired)       │
   │ item_vault   one md file per item   kanban_store  writes the board          │
   └───────────────────────────────┬────────────────────────────────────────────┘
                                    │ called by the edge adapters
      ┌──────────────┬─────────────┴─┬──────────────────┬────────────────┐
      ▼              ▼               ▼                  ▼                ▼
 intake_actions  pre_gate_actions  proposal_actions  delivery_actions  SKILL.md
 (plan/apply/     (route → path,    (gate handler:    (locate + send    (judgment
  verify: items    prep chain,       spawns fulfill    the deliverable   only: score
  + pre-gate       proposal card,    chain on          via hermes        dims, read
  graph)           auto paths)       approve)          send)             classifier,
                                                                         proposal prose)
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
workspace/model-routing) rather than touching the board itself. The four edge
adapters turn specs into cards through `KanbanStore` with retry-safe idempotency
keys: `intake_actions.py` (items + the complete pre-gate graph),
`pre_gate_actions.py` (route resolution, prep chain, proposal card),
`proposal_actions.py` (post-gate fulfillment chain), and `delivery_actions.py`
(final send). Calculations stay pure and unit-testable; board mutation stays in
reviewed, deterministic adapters rather than model-interpreted prose.

## Data flow of one item

1. A **scout** submits a draft through `scout_actions.py`; the helper validates
   it, writes to the config-scoped intake directory, and creates the `intake`
   card through the Hermes CLI (the scout only detects).
2. The intake worker runs `intake_actions.py plan` (parse + dedup, read-only),
   judges the per-dimension rubric scores (the model's ONE intake judgment),
   writes them to a scores file, and runs `intake_actions.py apply`.
3. `apply` validates scores through the engine, merges duplicates, creates vault
   items, and for each advancing item creates the **triage root** (parented to
   the intake card so the graph cannot dispatch half-built), the parallel
   **evidence lanes**, the **classifier** fan-in, and the **route** card — all
   engine-generated specs (`triage_root_spec`, `research_specs`,
   `classifier_spec`, `route_spec`) with intake-scoped idempotency keys that
   interoperate with `hermes kanban`-created cards. The triage worker later runs
   `intake_actions.py verify` (read-only) and completes itself to release the
   lanes — workers may only complete their own task id.
4. The route worker reads the classifier's value from its parent result (the
   model's second judgment: reading, not deciding) and runs
   `pre_gate_actions.py --classification <value>`, which resolves the path via
   `route.map`, records it on the item, and creates the linked **prep** chain
   plus a `propose:` card — or closes out an auto path (e.g. shelve) with no
   cards.
5. The proposal worker drafts the proposal (the model's third judgment: prose)
   and runs `delivery_actions.py send-proposal`, which **sends it to the human**
   as one `hermes send` attachment message and sets `awaiting_approval`.
6. The human replies; the orchestrator shells to `proposal_actions.py`, which
   reads `paths.<path>.fulfill` and spawns the **fulfillment** chain in a shared
   persistent workspace (`engine.fulfillment_specs`).
7. The engine writes the deliverable contract into every fulfillment stage's
   body (the configured `deliverable:` pattern resolves against the workspace
   ROOT, non-recursively) and instructs the FIRST stage to verify the layout
   with `delivery_actions.py deliver --dry-run` before completing. The FINAL
   stage's body carries the real delivery instruction: its worker runs
   `delivery_actions.py deliver`, which locates the configured deliverable in
   the persistent workspace, sends it to `<gate.target>` as a single attachment
   message, and records delivery on the item — or fails loudly so the worker
   blocks instead of improvising.

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
- New step type → add a method on `TriageEngine` returning `TaskSpec`s, applied
  by an adapter.
- New board mutation → extend the relevant adapter (and its tests + the
  synthetic eval), never a worker-session script. A worker that "needs" a
  one-off script has found an adapter gap; the fix belongs in the repo.

Adding your *subject matter* is never an engine change — it's `triage.yaml`.
