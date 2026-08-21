# 05 — Pipeline stages (and the gotchas to preserve)

Walks one item through the pipeline, pointing at the code for each stage and
calling out the hard-won gotchas you must not regress.

## Stage 1 — Intake (scout)

A scout runs under its source profile's gateway on a schedule, writes a draft,
and submits it through `scout_actions.py`. The helper validates the report,
derives `<workspace_root>/vault/intake/` from the selected config, writes only
there, and creates one `intake` card assigned to the orchestrator. **Scouts only
detect** — no dedup/score/route.

- Code: scout skill template; `engine/intake_parser.py` parses the report.
- ⚠️ **Gotcha — profile-local cron, scout-local gateway.** On Hermes 0.20,
  cron is owned by the profile that runs the scout, not by the orchestrator.
  Each scout profile registers its own `hermes -p <scout> cron create …`
  job and runs its own gateway so the cron scheduler ticks. The configured
  gateway profile keeps Kanban dispatch enabled (`kanban.dispatch_in_gateway: true`).
  Scout profiles disable it (`kanban.dispatch_in_gateway: false`) so they
  only generate intake cards; the configured gateway profile is the sole dispatcher.
  `python -m cli.triage scaffold` renders all of this.
- ⚠️ **Gotcha — cron starts the board through the CLI.** Tools are bound to
  execution surfaces, not profiles. Cron scouts have no `HERMES_KANBAN_TASK`, so
  they do not receive the `kanban_*` tools that the dispatcher injects into
  workers. Enable `terminal` on the scout's `cron` surface and have the scout run
  `scout_actions.py`, which runs `hermes kanban --board <board> create ... --json`
  after writing the canonical report.
  The scaffold emits both
  `hermes -p <scout> tools enable … --platform cli` and `--platform cron` for
  cron-owning profiles. Execute the generated cron command only after preflight
  confirms the cron surface and every configured toolset name is available.
  The configured list lives in `hermes.profiles.<name>.toolsets` in
  `triage.yaml`.

## Stage 2 — Dedup (orchestrator → engine)

For each candidate, `TriageEngine.dedup(text)` ranks existing vault items.
`duplicate` → append source, stop. `possible` → flag, continue. `new` → create
the item file (`ItemVault.create_item`, `status: triage`).

- Code: `engine/dedup.py`, `engine/item_vault.py`.

## Stage 3 — Score (orchestrator judgment + engine)

The orchestrator scores each rubric dimension (using `TriageEngine.rubric_prompt()`)
and hands the breakdown to `TriageEngine.score()`, which applies maxes + threshold.
Below threshold → the orchestrator skill says to **shelve without bothering the
human**. This is model-applied behavior, not an engine side effect. Write both
`score` and `score_breakdown` to the item file either way.

- Code: `engine/scoring.py`.

## Stage 4 — Research fan-out (engine generates lane specs)

`TriageEngine.research_specs(slug, triage_id)` returns parallel evidence specs,
all parented to the supplied triage id. `classifier_spec(slug, evidence_ids)`
returns a classifier parented to every evidence lane; the route card is parented
to that classifier. All use the project `dir` workspace so they can read the
vault and domain files. The intake orchestrator creates that triage root parented
to its current intake card, immediately records its id in the item's
`linked_kanban_tasks`, then creates this graph. This keeps the root in `todo` until the graph
is complete and prevents a second orchestrator from racing the fan-out. Once the
intake completes, the triage worker verifies the existing graph, creates nothing,
and completes itself to release the evidence lanes together. Hermes workers may only
complete their own task id. The engine does not create the route card or apply
these board transitions, so this remains model-applied orchestration rather than
an engine guarantee. All graph creates use intake-scoped idempotency keys.
Archived cards are audit history and never satisfy a new intake graph.
Dispatcher-spawned orchestrators use injected `kanban_*` tools; cron scouts are
the only workers that create cards through the Kanban CLI.

- ⚠️ **This is the fan-in pattern** — the classifier auto-fires when the last
  evidence lane finishes; route follows classifier. No polling. See docs/02.

## Stage 5 — Route (engine)

The classifier lane emits a value (`route.classifier`). `TriageEngine.route(value)`
maps it to a path name; the orchestrator writes `path: <name>` on the item. An
`auto` path (e.g. `shelve`) ends here.

- Code: `engine/routing.py`.

## Stages 6–7 — Prep + propose

`TriageEngine.prep_specs(slug, path)` returns ordered pre-gate specs with no
parent links. `pre_gate_actions.py` resolves abstract roles to configured
profiles, creates the chain idempotently, and appends a `propose:` card parented
to the last prep card (or route card when prep is empty). That proposal worker
drafts from `paths/proposals/<path>.md`, sets `status: awaiting_approval`, and
**sends it**.

The explicit proposal card is the ordering boundary: the route worker completes
after scheduling the chain, and Kanban promotes the proposal only after every
prep dependency is done.

- ⚠️ **Gotcha — delivery ≠ status.** The orchestrator is a headless worker. It
  MUST run `hermes send --to <gate.target> --file <proposal>`; setting the status field
  notifies no one. (The first live run of the origin system produced proposals
  that never reached the human because of exactly this.)

## Stage 8 — Human gate

The human replies (verbs from `gate:`). The orchestrator shells to
`proposal_actions.py {approve|shelve|shelve-all|modify}`.

- ⚠️ **Gotcha — no leading slash.** `/approve` is Hermes's command-execution
  approval command, not the pipeline gate. Reply with ordinary text: `approve <slug>`.
- The gate is **non-blocking**: while waiting, the orchestrator processes other
  items.

## Stages 9–11 — Fulfill + deliver

On `approve`, `proposal_actions.py` reads `paths.<path>.fulfill` and spawns the
chain via `TriageEngine.fulfillment_specs()`.

- The triage-root link is audit metadata used for the approval comment. A missing
  link no longer blocks an otherwise valid approval or fulfillment chain.

- ⚠️ **Gotcha — persistent workspace.** Every fulfillment stage runs with
  `workspace_kind="dir"` pointed at the SAME `work/<subdir>/<slug>/`. Scratch
  workspaces are wiped between tasks, which strands the final delivery step. The
  engine already does this — don't switch it to scratch.
- ⚠️ **Gotcha — first stage `ready`.** The first fulfillment card has no blocking
  parent so it lands `ready`; the rest chain off it. This deliberately avoids
  depending on the lifecycle of the pre-gate triage/root card.
- The orchestrator skill instructs a later model turn to deliver after the final
  stage. There is no deterministic completion hook or correlation adapter in
  this repository that guarantees the send.

## Cost gate (cross-cutting)

`scripts/cost_report.py <slug>` is a standalone probe that sums per-item spend
from board telemetry and compares it to `cost_gate_usd`. The orchestrator and
engine do not call it automatically, so pause/notify policy is not currently
enforced. It degrades to "telemetry unavailable" if your Hermes build doesn't
expose cost columns; adjust the SQL there for your schema.

## Failure handling

- Worker crash → the dispatcher reclaims/respawns per Hermes's respawn guard.
  (Higher-level "self-healing" — e.g. regenerating artifacts lost to a scratch
  dir — is orchestrator-skill behavior you write, not core Hermes.)
- Missing tool / ambiguous state → the worker should block the card with a reason,
  not guess.
- Be honest in completion metadata. Don't fake a green test.
