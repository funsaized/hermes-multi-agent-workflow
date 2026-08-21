# 02 — The board (how the fleet coordinates)

The pipeline has no message bus and no inter-agent chat. **The Hermes Kanban board
is the bus and the audit log.** Every unit of work is a card; every agent reads
and writes the same board.

## Cards and columns

A card (task) is mostly: a **title**, a **body** (instructions), an **assignee**
(which profile runs it), a **status** (`todo → ready → running → done`), and
optional **parent links**.

`engine/kanban_store.py` is the thin writer over the board's SQLite DB. You rarely
edit it.

## Assignee = routing

The whole multi-agent mechanism is a name on a card. A card assigned to `builder`
runs on the builder profile; one assigned to `video_producer` runs on that
profile. The engine maps abstract **roles** (in `triage.yaml`) to real **profiles**
via the `roles:` block, so your config talks in roles and the board gets profiles.

## The dispatcher

The configured Hermes **gateway** runs the sole dispatcher loop: it finds `ready`
cards across boards, claims one atomically, spawns the assigned agent in its own
workspace, and marks the card `done` when the agent finishes — then repeats.
Cron-owning scout gateways have dispatch disabled; keep the configured dispatcher
gateway and each scout scheduler gateway running (`docs/07-runbook.md`).

## Fan-in (the part that makes it event-driven)

A card with parents starts in `todo` and **auto-promotes to `ready` only when
every parent is `done`.** That single rule gives you parallelism + sequencing with
no polling:

- The research lane specs are all parented to the triage card. They become
  runnable together only after that parent is marked `done`.
- A classifier card is parented to all evidence lanes; `route` is parented to
  the classifier and fires after synthesis finishes.
- The post-gate fulfillment chain links each stage to the previous → they run in
  order.

`engine.research_specs()` records the supplied root parent on each evidence spec;
`classifier_spec()` records every evidence task as a parent; `route_spec()`
parents the route card to the classifier. `intake_actions.py apply` creates that
entire pre-gate graph deterministically. `engine.fulfillment_specs()` returns
ordered specs with empty parents; `proposal_actions.py::action_approve` creates
those cards and links each later stage to its predecessor. `pre_gate_actions.py`
similarly links the prep stages and appends a proposal card, so proposal
delivery cannot run before prep. The only board transition a model applies is
completing ITS OWN task (a Hermes worker-protocol constraint) — e.g. the triage
root completes itself after `intake_actions.py verify` confirms the graph.

## Board gotchas the engine already handles

1. **First post-gate task is deliberately `ready`, not `todo`.** The handler
   creates it with *no* parent and chains the rest. This keeps post-gate readiness
   independent of the already-completed pre-gate root card.

2. **`KanbanStore.create_task`** sets status from parents: none → `ready`, some →
   `todo`. That's the whole promotion contract; respect it when you create cards
   yourself.

3. **Idempotency interoperates with the Hermes CLI.** Cards created via
   `hermes kanban ... --idempotency-key` carry native ids with the key in the
   `idempotency_key` column. `create_task` looks that column up FIRST (ignoring
   archived cards — history never satisfies a key) and only then falls back to
   its deterministic hashed id, so an adapter re-run never duplicates a card a
   cron scout already created.

4. **Model routing is per-card, not per-profile.** When `roles:` or a stage
   entry sets `model` / `provider` / `reasoning_effort`, the adapters write the
   board's `model_override` / `provider_override` / `reasoning_effort` columns
   (skipped safely on schemas without them). Profiles are never mutated.

## Dedicated board

Each pipeline uses its own board (`board:` in `triage.yaml`), isolated from
`default`, so triage cards don't mix with unrelated work. Named boards live at
`<hermes-home>/kanban/boards/<slug>/kanban.db`; the back-compat `default` board
is `<hermes-home>/kanban.db`. The Hermes home is `$HERMES_HOME` when set, else
the first existing of `~/.hermes` and `%LOCALAPPDATA%/hermes` (Windows installs
use the latter). `engine.kanban_store.resolve_board_db()` handles all of this
and honors the dispatcher-injected `HERMES_KANBAN_DB`; **never hardcode a board
path** in a script or task body.

## Version note

`kanban_store.py` writes the board schema directly. It REQUIRES the core
columns (`id, title, body, assignee, status, priority, created_by, created_at,
workspace_kind, workspace_path, consecutive_failures`) plus `task_links`,
`task_comments`, `task_events`, and OPTIONALLY writes `idempotency_key`,
`skills`, `model_override`, `provider_override`, and `reasoning_effort` when
the installed schema has them (probed per connection, never assumed). If a
future Hermes release changes the core columns, update the adapter. The
dispatcher + cron live **inside the gateway** (the old standalone
`hermes kanban daemon` is deprecated).
