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

## Stages 2–4 — Dedup, score, fan-out (`intake_actions.py`)

The intake worker runs exactly three steps:

1. `python intake_actions.py --config <cfg> plan --intake <report>` — read-only.
   Parses candidates, derives canonical slugs (`engine.item_vault.slugify` — the
   ONE slug implementation; never hand-roll one), ranks vault similarity per
   candidate, and prints the rubric prompt.
2. The model judges per-dimension scores (its ONE intake judgment) and writes
   `<report>.scores.json`.
3. `python intake_actions.py --config <cfg> apply --intake <report> --scores
   <scores> --intake-task $HERMES_KANBAN_TASK` — validates scores through
   `TriageEngine.score()` (maxes + threshold), merges `duplicate` candidates
   into their existing items, creates vault items (below threshold →
   `status: shelved_below_threshold`, **without bothering the human**), and for
   each advancing item creates the triage root, evidence lanes, classifier
   fan-in, and route card.

Graph guarantees `apply` enforces (these used to be model-applied; regressions
here are caught by the synthetic eval):

- The triage root is parented to the current intake card, so it stays `todo`
  until the intake completes — a second orchestrator can never race a
  half-built graph.
- The root's id is written to the item's `linked_kanban_tasks` BEFORE the lane
  fan-out (the durable audit link gate actions read).
- Every card uses an intake-scoped idempotency key
  (`<pipeline>:triage|research|route:<intake-id>:<slug>[:<lane>]`) that
  interoperates with `hermes kanban`-created cards; re-running `apply` is safe.
- Archived cards are audit history and never satisfy a new intake graph.
- The route card is parented only to the classifier and carries the
  orchestrator skill; lanes and classifier do not.

The `triage:` release barrier then runs
`python intake_actions.py --config <cfg> verify --slug <slug>` (read-only) and
completes ITSELF — Hermes workers may only complete their own task id, which is
the one board transition that stays model-applied.

- ⚠️ **This is the fan-in pattern** — the classifier auto-fires when the last
  evidence lane finishes; route follows classifier. No polling. See docs/02.
- Code: `intake_actions.py`, `engine/dedup.py`, `engine/item_vault.py`,
  `engine/scoring.py`, `engine/engine.py`.

## Stage 5 — Route (`pre_gate_actions.py --classification`)

The classifier lane emits a value (`route.classifier`). The route worker READS
that value from the classifier's result — its only judgment — and passes it to
`pre_gate_actions.py --classification <value>`, which resolves the path via
`TriageEngine.route()` (unknown values raise, loudly), writes `path:` and
`classified_as:` on the item, and closes out `auto` paths (e.g. `shelve`,
`status: auto_shelve`) with no cards.

- Code: `engine/routing.py`, `pre_gate_actions.py`.

## Stages 6–7 — Prep + propose

`TriageEngine.prep_specs(slug, path)` returns ordered pre-gate specs with no
parent links. `pre_gate_actions.py` resolves abstract roles to configured
profiles, creates the chain idempotently, and appends a `propose:` card parented
to the last prep card (or route card when prep is empty). That proposal worker
drafts from `paths/proposals/<path>.md`, renders it to
`<workspace>/proposals/<slug>.md`, and runs
`python delivery_actions.py --config <cfg> send-proposal <slug>`. The adapter
sends ONE gate message with the file as a native attachment plus a short
deterministic caption (title, path, score, reply line), sets
`status: awaiting_approval`, and records `proposal_sent_at` — re-runs are
idempotent no-ops (`--resend` overrides for redrafts).

The explicit proposal card is the ordering boundary: the route worker completes
after scheduling the chain, and Kanban promotes the proposal only after every
prep dependency is done.

- ⚠️ **Gotcha — delivery ≠ status.** Setting the status field notifies no one.
  (The first live run of the origin system produced proposals that never reached
  the human because of exactly this.) The send-proposal adapter does both,
  status AFTER a successful send, so a failed send stays retry-safe.
- ⚠️ **Gotcha — never send a proposal as message text.** `hermes send --file`
  sends file *contents* as text, which Discord chunks into a burst of 2000-char
  messages; a 17 KB proposal reliably trips the platform rate limit that way
  (and an improvised worker retry loop then trips the *global* bucket). The
  adapter's `MEDIA:` attachment path is one message regardless of length, with
  a bounded 15s/45s/120s backoff if a 429 still occurs.

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

## Stage 12 — Delivery (`delivery_actions.py`)

The engine appends a deterministic instruction to the FINAL fulfillment stage's
task body: run `python delivery_actions.py --config <cfg> deliver <slug>` (the
pre-split no-subcommand form still works). The adapter

1. requires the item to be `approved` (the gate is real — unapproved work is
   refused; a `delivered` item is an idempotent no-op),
2. resolves the deliverable inside the persistent workspace —
   `paths.<path>.deliverable` (filename or glob, exactly one match), falling
   back to a single `deliverable.*` file, otherwise failing with a listing of
   what IS in the workspace,
3. sends it through the configured Hermes channel as ONE attachment message
   (caption + `MEDIA:<path>`, bounded 429 backoff) — the same messaging
   contract as the proposal send, and
4. marks the item `delivered` and comments on the triage root.

If it exits non-zero the worker blocks its task with the JSON error. Nobody
sends deliverables by hand; setting a status field notifies no one (that gotcha
produced the origin system's silent-proposal incident, and delivery had the
same hole until this hook).

## Cost gate (cross-cutting)

`scripts/cost_report.py <slug>` is a standalone probe that sums per-item spend
from board telemetry and compares it to `cost_gate_usd`. The orchestrator and
engine do not call it automatically, so pause/notify policy is not currently
enforced. It degrades to "telemetry unavailable" if your Hermes build doesn't
expose cost columns; adjust the SQL there for your schema.

## Synthetic eval (cross-cutting)

`python scripts/run_synthetic_eval.py` replays every stage above — including
the auto path, an unknown classification, idempotent re-runs, model routing,
and both delivery failure modes — against a temp board using the live Hermes
schema, with the model's judgments played by fixtures. It also runs inside
`python -m unittest discover -s tests`. Any change to a stage's guarantees must
keep it green.

## Failure handling

- Worker crash → the dispatcher reclaims/respawns per Hermes's respawn guard.
  (Higher-level "self-healing" — e.g. regenerating artifacts lost to a scratch
  dir — is orchestrator-skill behavior you write, not core Hermes.)
- Missing tool / ambiguous state / non-zero adapter exit → the worker blocks the
  card with the adapter's JSON error as the reason. It never guesses, retries
  with hand-rolled SQL, or writes a one-off script — an adapter gap is a repo
  bug to fix (with tests and the eval), not something to work around in a
  session.
- Be honest in completion metadata. Don't fake a green test.
