# 08 — End-to-end system guide and architectural assessment

## Executive verdict

This repository is a reusable design skeleton for a durable, single-host,
multi-agent triage pipeline on Hermes:

> detect candidates → deduplicate → score → research in parallel → route → prepare
> a proposal → stop at one human gate → fulfill → deliver

Its central design choice is good: domain decisions live in `triage.yaml`, crisp
workflow mechanics live in Python, and Hermes supplies profiles, scheduling,
durable task state, worker dispatch, and messaging.

However, distinguish four maturity levels:

| Level | Current state |
|---|---|
| Architecture template | Strong and well documented |
| Generic task-spec engine | Implemented; config validates and 12 unit tests pass |
| Hermes 0.20 integration | Partial; several setup and lifecycle contracts are stale or implicit |
| Deployable end-to-end service | Not yet; the repo itself calls `init`/`install` stubs and `scaffold` only a review plan |

The repository should therefore be treated as a basis for the real system, not as
an application that can be enabled unchanged.

## 1. The system in one picture

```text
                       DOMAIN / POLICY PLANE
                ┌─────────────────────────────────┐
                │ triage.yaml                     │
                │ sources, rubric, lanes, routes, │
                │ paths, roles, gate, cost limit  │
                └───────────────┬─────────────────┘
                                │ validate/load
                                v
                     DETERMINISTIC ENGINE
     ┌───────────────────────────────────────────────────────┐
     │ config  dedup  scoring  routing  task-spec generation │
     │ item vault  intake parser  workspace selection        │
     └───────────┬───────────────────────┬───────────────────┘
                 │ task specifications   │ item state
                 v                       v
             CONTROL PLANE            DATA PLANE
     ┌───────────────────────┐   ┌──────────────────────────┐
     │ Hermes Kanban board   │   │ Markdown item vault      │
     │ tasks, dependencies,  │   │ intake reports           │
     │ status, comments,     │   │ persistent path outputs  │
     │ runs, events          │   │ proposals/deliverables   │
     └───────────┬───────────┘   └──────────────────────────┘
                 │ dispatcher claims ready cards
                 v
              EXECUTION PLANE
     ┌───────────────────────────────────────────────────────┐
     │ Hermes profiles                                      │
     │ scouts | orchestrator | researchers | builders | QA  │
     │ each with model, tools, skills, auth, memory          │
     └───────────┬───────────────────────────────────────────┘
                 │ proposal / result
                 v
              HUMAN PLANE
     ┌───────────────────────────────────────────────────────┐
     │ Telegram or another Hermes gateway channel            │
     │ approve | shelve | modify                              │
     └───────────────────────────────────────────────────────┘
```

There is no separate message broker. The Kanban database is the durable control
bus. Files are the artifact plane. Telegram is for the human decision, not for
agent-to-agent coordination.

Evidence: `README.md:3-17`, `docs/01-architecture.md:3-30`,
`docs/02-the-board.md:1-28`.

## 2. What belongs where

### 2.1 `triage.yaml`: domain and policy

`triage.yaml` defines:

- which sources are watched and on what schedule;
- what fields constitute an item;
- similarity thresholds;
- the value rubric and advancement threshold;
- independent research lanes;
- classifier values and their destination paths;
- pre-gate and post-gate stages;
- abstract roles and their Hermes profile mappings;
- the human channel and reply verbs;
- the persistent workspace root and soft cost threshold.

This is the main adaptation surface. Changing from “AI-agent pain points” to bug
triage, roadmap research, product opportunities, security findings, support
escalations, or content production should primarily change this file and the
Markdown templates it references—not `engine/`.

Evidence: `triage.yaml:17-181`, `engine/config.py:114-128`.

### 2.2 `engine/`: generic deterministic mechanisms

The engine is intentionally model-free where exact behavior is possible:

- `config.py` loads YAML into typed dataclasses and validates references;
- `dedup.py` compares a candidate with persisted items;
- `scoring.py` validates a model-proposed score breakdown and applies the bar;
- `routing.py` maps a normalized classifier value to a path;
- `engine.py` builds research, prep, and fulfillment task specifications;
- `item_vault.py` persists item lifecycle state;
- `intake_parser.py` parses scout reports;
- `frontmatter.py` serializes the item files;
- `kanban_store.py` is the current side-effect adapter to the Hermes board.

The boundary is: models judge; code calculates, validates, and constructs.

Evidence: `engine/engine.py:1-20`, `docs/01-architecture.md:32-49`.

### 2.3 Skills: model behavior

The scout skill tells a scheduled source-specific profile how to search, what
evidence to retain, how to write the intake report, and how to create one intake
card. It must not score or route.

The orchestrator skill is the model-driven coordinator. It supplies the genuinely
fuzzy judgments:

- dimension-by-dimension scoring;
- interpretation of research output;
- proposal prose;
- interpretation of the human’s reply.

Everything else is supposed to call the engine or Hermes Kanban.

Evidence: `skills/templates/triage-scout/SKILL.md:40-90`,
`skills/templates/triage-orchestrator/SKILL.md:14-105`.

### 2.4 Hermes: runtime substrate

Hermes contributes mechanisms this repository does not implement:

- **profiles**: isolated role identities with their own model, tools, skills,
  configuration, auth, memory, and sessions;
- **cron**: durable scheduled scout execution;
- **Kanban**: task state, dependencies, comments, run history, crash recovery,
  workspaces, and dispatcher claims;
- **gateway**: long-running cron and Kanban runtime plus inbound messaging;
- **send**: outbound delivery through Telegram/Discord/etc.;
- **dashboard/CLI**: human observability and recovery.

The installed runtime inspected for this assessment is Hermes Agent 0.20.0
(2026.8.3).

Official references:

- <https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban>
- <https://hermes-agent.nousresearch.com/docs/user-guide/features/cron>
- <https://hermes-agent.nousresearch.com/docs/user-guide/profiles>

## 3. State model: board state versus artifact state

The system deliberately has two sources of state, with different ownership.

### Board: lifecycle authority

The board should answer:

- What work exists?
- Which profile owns it?
- Is it waiting, runnable, active, blocked, or done?
- What are its dependencies?
- What happened on each attempt?
- What handoff did the parent provide?

Current Hermes statuses include `triage`, `todo`, `ready`, `running`, `blocked`,
`done`, and `archived`.

### Files: domain and artifact authority

The item vault should answer:

- What is the candidate and its evidence?
- What score and route were chosen?
- Is human approval pending?
- What notes and decisions accumulated?
- Which board cards belong to the item?
- Where are the resulting artifacts?

An item starts with the spine in `engine/item_vault.py:76-88`:

```text
slug, title, first_seen, sources,
score, score_breakdown, path, status,
embedding, linked_kanban_tasks, cost_spent_usd
```

The board is not a substitute for the vault, and the vault is not a task
scheduler. A reliable implementation must update both deliberately and recover
when one write succeeds and the other does not.

## 4. One item’s intended lifecycle

### Stage 1 — Detect and intake

A cron runs a source profile with its scout skill. The scout:

1. searches its assigned surface;
2. keeps source-backed candidates;
3. writes one report;
4. creates one `intake` card assigned to the orchestrator.

The report/card split is intentional: the card is a durable wake-up signal; the
report is the potentially large evidence artifact.

Evidence: `skills/templates/triage-scout/SKILL.md:45-81`.

### Stage 2 — Deduplicate

The orchestrator parses candidates and calls `TriageEngine.dedup()`.

```text
similarity >= duplicate_threshold → append source to existing item; stop
similarity >= possible_threshold  → flag; continue
otherwise                         → create a new item
```

The bundled implementation is bag-of-words token cosine, not semantic embedding
search. It reads every Markdown item in the vault and returns the top K matches.

Evidence: `engine/dedup.py:42-74`, `engine/dedup.py:89-117`.

### Stage 3 — Score

The model receives a generated rubric prompt, proposes one number per dimension,
and gives the breakdown back to deterministic code. The engine:

- fills missing dimensions with zero;
- converts numeric-looking values to integers;
- clamps each score to `0..max`;
- ignores unknown keys with notes;
- sums the result;
- advances only if it reaches the configured threshold.

Below-threshold items are intended to be auto-shelved without bothering the
human.

Evidence: `engine/scoring.py:44-92`.

The heuristic scorer is not generic. It contains reference-domain keywords and
field interpretations. For a new domain, use model judgment plus deterministic
validation unless you explicitly implement a new heuristic.

Evidence: `engine/scoring.py:95-158`.

### Stage 4 — Research fan-out

For an advancing item, the intended DAG is:

```text
                      ┌─ verify_sources ─────────────┐
triage/root completed ├─ prior_context ──────────────┼─> route
                      └─ existing_solutions_audit ──┘
                                           classifier
```

Each lane is independent and can run concurrently. The route task depends on all
lane cards, so Kanban promotes it only after every parent is done. This is the
core fan-out/fan-in pattern.

`TriageEngine.research_specs()` creates lane specs and marks the classifier lane’s
contract in its task body.

Evidence: `engine/engine.py:95-123`.

### Stage 5 — Route

The classifier emits one configured value. `TriageEngine.route()` normalizes its
case and looks it up in `route.map`; unknown values fail loudly.

In the worked example:

```text
missing / broken                 → build
confusing / poorly_documented    → video
outdated                         → video
good                             → shelve
```

Evidence: `triage.yaml:113-121`, `engine/routing.py:15-28`.

### Stages 6–7 — Prepare and propose

The selected path defines pre-gate stages. The model workers gather the material
needed for a decision, then the orchestrator fills the path’s proposal template,
sets the item to `awaiting_approval`, and explicitly sends the message.

A file status does not notify anyone. Delivery must be a real gateway send.

Evidence: `triage.yaml:132-158`,
`skills/templates/triage-orchestrator/SKILL.md:71-80`.

### Stage 8 — Human gate

The human chooses one of three semantic actions:

- **approve**: start the configured fulfillment chain;
- **shelve**: terminate the item;
- **modify**: create an immediate proposal-redraft task and return to the gate.

`proposal_actions.py` refuses actions unless the item is exactly
`awaiting_approval`, which prevents accidental approval outside an active gate.

Evidence: `proposal_actions.py:81-84`, `proposal_actions.py:92-240`.

### Stages 9–11 — Fulfill and deliver

Approval constructs the path’s fulfillment stages. Every stage shares one
absolute persistent directory:

```text
<workspace_root>/<path.workspace_subdir>/<item-slug>/
```

The first stage has no parent and is immediately `ready`; every later stage
depends on the prior one.

```text
approve
  │
  ├─ build: prototype_build → test_run → final_report → deliver
  │
  └─ video: slides_builder → script_writer → final_deliverable → deliver
```

Persistent `dir` workspaces are load-bearing. Hermes deletes undeclared scratch
files after task completion. A shared directory preserves artifacts across
roles.

Evidence: `engine/engine.py:139-179`, `proposal_actions.py:108-151`.

Note that the example “video” path creates slides and a script; it does not render
or publish an actual video (`paths/specs/video.md:8-13`).

## 5. Hermes Kanban mechanics

### Dependency promotion

Hermes creates a no-parent task as `ready`. A task with any unfinished parent is
`todo`. Completing the last parent makes the child eligible for promotion.

```text
no parents                  => ready
unfinished parent(s)        => todo
all parents done            => ready
claimed by dispatcher       => running
successful worker handoff   => done
needs intervention          => blocked
```

This behavior is implemented by current Hermes’ canonical `create_task()` and
`complete_task()` APIs. The repository’s direct adapter reproduces only a subset.

### Worker dispatch

The gateway dispatcher:

1. sweeps all boards;
2. reclaims stale/crashed claims;
3. promotes eligible children;
4. atomically claims ready tasks;
5. resolves the assignee to a Hermes profile;
6. launches a worker in the specified workspace;
7. records a run and completion/block evidence.

A profile name on the card is therefore executable routing, not just metadata.

### Why Kanban rather than `delegate_task`

`delegate_task` is an in-process fork/join primitive. Kanban is durable and can
survive agent/session restarts, retain an audit trail, involve named profiles,
wait for humans, and recover/retry work. This pipeline needs Kanban.

## 6. Profiles and least privilege

The example maps six abstract roles to profiles and adds two scout profiles:

| Profile | Responsibility | Minimum capabilities |
|---|---|---|
| `xresearch` | X source detection | X/web search, files, Kanban create |
| `webresearch` | web source detection | web, files, Kanban create |
| `orchestrator` | lifecycle judgment and routing | files, terminal/Python, Kanban, outbound messaging |
| `researcher` | parallel evidence lanes | web, files, Kanban completion |
| `analyst` | problem synthesis/ideation | web/files, Kanban completion |
| `builder` | approved implementation | file/terminal/coding, Kanban completion |
| `tester` | independent verification | file/terminal/test tools, Kanban completion |
| `video_producer` | tutorial/deck/script work | web/files/design tools, Kanban completion |

Profiles isolate model/provider choice, credentials, skills, sessions, memory, and
toolsets. They are not filesystem sandboxes: workers run as the same OS user.
Boards are the hard task-visibility boundary; tenants are only soft namespaces.

## 7. Configuration validation: what it proves and what it does not

`python -m cli.triage validate` currently proves:

- every route target is a defined path;
- every role used by research/path stages is mapped;
- the score threshold is reachable;
- the classifier lane exists;
- referenced template paths are reported if missing.

Evidence: `engine/config.py:208-241`, `cli/triage.py:30-54`.

It does not currently prove:

- required item fields (`title`, `claim`, `sources`) exist;
- source IDs/profile names/skill names are unique and valid;
- dedup thresholds are ordered and in range;
- schedules are parseable by current Hermes;
- path templates are valid for their path type;
- auto paths do not also define work;
- a full task graph is acyclic and dispatchable;
- configured profiles, skills, boards, toolsets, auth, or channels exist;
- runtime command syntax matches the installed Hermes version.

## 8. Security and failure boundaries

### Trust boundary

The high-risk path is:

```text
untrusted web content
        ↓
scout report
        ↓
model context
        ↓
proposal + human approval
        ↓
privileged build/test tools
```

The human gate and path rails are the controls separating hostile source content
from privileged fulfillment. They reduce risk; they do not sanitize prompt
injection by themselves.

### Controls already represented

- one mandatory human gate before fulfillment;
- separate profiles with intended least privilege;
- hard path rails inlined into worker tasks;
- dedicated board isolation;
- persistent workspaces only where artifacts must survive;
- ignored secrets, databases, vaults, and generated work;
- explicit failure on unknown route values;
- gate actions conditioned on item status.

Evidence: `docs/06-security.md:8-64`, `.gitignore:1-14`.

### Operational controls still needed

- treat scope rails as policy, not merely prompt text;
- add runtime/attempt/token budgets per task;
- add idempotency keys to all automation-created cards;
- use independent verification before delivery;
- use canonical Kanban APIs so runs, events, promotions, attachments, and
  notifications remain coherent;
- ensure web researchers cannot reach secrets or privileged mutation tools;
- define exactly what “deliver” means for each path (local artifact, message,
  branch, PR, publication, deployment).

## 9. Current implementation gaps on Hermes 0.20.0

These are the differences between the intended architecture and what is actually
wired today.

### P0 — Dispatched tasks do not receive the repository or item paths

The scout creates the intake card with the default scratch workspace. The
orchestrator skill then says all commands run from the repo root, but no task
pins that repo as `dir:<absolute-path>`. Research/prep task bodies say “read the
item file” without including its absolute path. Fulfillment workers share the
output directory, but the vault is elsewhere and again not named absolutely.

Consequences:

- an orchestrator worker may not find `triage.yaml`, `engine/`, or the scout report;
- researchers may not find the item file;
- fulfillment workers may not find the approved proposal/evidence.

Smallest fix shape: give the board a project/default workdir or put explicit
absolute repo/vault/item paths into every task specification; choose one
workspace contract and test it end to end.

Evidence: `skills/templates/triage-scout/SKILL.md:52-64`,
`skills/templates/triage-orchestrator/SKILL.md:23-24`,
`engine/engine.py:114-121`, `engine/engine.py:165-177`.

### P0 — (partially resolved) The setup plan contained stale current-Hermes commands

The scaffold/preflight/render-skills surfaces are now modern. The planner
emits only the Hermes 0.20 surface, and `preflight` verifies it against the
installed CLI:

- profile cloning uses `--clone-from` (not `--from`) and `--no-alias`;
- the planner emits `hermes -p <profile> …` for every profile-scoped step;
  aliases are optional convenience only;
- `hermes cron create` has no `--profile` option; the scaffold now emits
  `hermes -p <scout> cron create '<schedule>' '<prompt>' --name … --skill …
  --workdir <abs> --deliver local`, with cron owned by the profile that runs
  the scout;
- `hermes tools enable` is rendered with `--platform cli` and, immediately
  afterward for cron-owning profiles, `--platform cron`; the opening runtime
  checkpoint requires the read-only cron-surface and toolset-name capability
  checks to pass before a human executes either mutating phase;
- board creation uses `hermes kanban boards create … --default-workdir <abs>`;
- gateway install uses `hermes -p <profile> gateway install --start-now
  --start-on-login`; only the orchestrator keeps
  `kanban.dispatch_in_gateway: true`; cron-owning scout profiles set it
  `false`;
- skill installation is a `CHECKPOINT:` with both reviewed-local-copy and
  future-profile-distribution paths; automatic `hermes profile install` is
  intentionally deferred;
- model selection, provider auth, and gate-channel auth are surfaced as
  `CHECKPOINT:` blocks, never as invented commands.

`preflight` reads only the installed CLI's stable help/list/status surfaces
(no `.env`/`auth.json`/board DB access), and returns exit 1 with a structured
`PreflightReport` (`text` or `json`) whenever any of the above resources are
absent or drifted. Toolset availability is checked separately from enabled
state, so an unknown name is reported before a silent `tools enable` no-op.
`tests/test_hermes_cli_contract.py` is an opt-in live
contract test that re-derives every generated surface and checks every
generated long flag against the installed help; the pure planner tests remain
authoritative for exact argv.

**Verification contract:** the pure planner, preflight, materialization, and
rehearsal-classifier suites pass. Two live checks remain opt-in:

- `test_generated_commands_and_flags_exist_on_installed_help_surface` skips
  unless `HERMES_RUN_CLI_CONTRACT=1` is set; on installed Hermes 0.20.0 it
  passes (every generated subcommand and long flag is present).
- `test_generated_topology_in_disposable_home` skips unless
  `HERMES_RUN_DISPOSABLE_REHEARSAL=1` is set; on installed Hermes 0.20.0 it
  is an **explicit skip after cleanup**, not a pass, because Hermes 0.20.0
  reports `Unknown toolset 'kanban'` (exit 0, no error) and exposes
  `code_execution` in place of the configured `coding`. Translating those
  names is outside this repository's scope and was deliberately not done;
  the integration test surfaces the drift as the precise blocker it is and
  refuses to claim readiness. It skips only when each missing configured name
  is genuinely absent from the installed availability set; an available but
  disabled toolset is a product failure.

So this finding is "scaffold/preflight/render-skills modernized" — the stale
command surface is gone — but the **runtime toolset names are still out of
sync** with installed Hermes 0.20, and that sync is blocked on an upstream
Hermes fix (issue #64494) rather than anything in this repository. Until the
toolset names or `triage.yaml` reconcile, `preflight --format json` will
continue to report missing-`kanban` and missing-`coding` blockers on a fresh
apply of the scaffold.

Live evidence at audit time: `python -m cli.triage scaffold --format shell`
and `python -m cli.triage preflight --format json` ran against the installed
Hermes 0.20.0 home; preflight reported the expected undeployed-resource and
toolset-name blockers (board, profiles, skills, toolsets, gateways, cron jobs,
Telegram gate target), which is the shape the verifier should return before
the scaffold is applied.

Evidence: `engine/scaffold.py`, `engine/hermes_preflight.py`,
`engine/skill_materialization.py`, `cli/triage.py`, `tests/test_scaffold.py`,
`tests/test_preflight.py`, `tests/test_render_skills.py`,
`tests/test_hermes_cli_contract.py`,
`tests/integration/test_scaffold_disposable_home.py`,
`docs/07-runbook.md`, `handoffs/impl-a.md`, `handoffs/impl-b.md`,
`handoffs/impl-c.md`.

### P0 — The full pre-gate DAG is not actually constructed by code

`research_specs()` returns only lane cards. The route fan-in card is manually
created by the orchestrator skill. `prep_specs()` returns ordered specs, but each
spec has empty parents; it does not encode the chain. No implemented adapter
turns those specs into a linked pre-gate DAG. Only post-gate approval has concrete
card-creation code.

The system therefore relies on a model correctly improvising deterministic board
mechanics—the exact behavior the “fat engine, thin skill” design intends to avoid.

Evidence: `engine/engine.py:97-123`, `engine/engine.py:132-179`,
`skills/templates/triage-orchestrator/SKILL.md:58-74`.

### P0 — Root-task completion semantics are ambiguous

Research lanes are parented to the triage task, so they remain `todo` until that
root is `done`. The skill says to create the root and lanes but does not define
when/how the root is completed. Elsewhere it describes the triage task as
still open at the gate. Those two interpretations conflict.

Smallest reliable shape: do not use one card as both a completed fan-out trigger
and an intentionally open lifecycle anchor. Use a completed `research_seed` card
for lane parents and a separate item/gate anchor, or create the lanes ready and
track the item root without making it a dependency.

Evidence: `engine/engine.py:97-123`, `docs/02-the-board.md:30-50`.

### P0 — The human reply path is prose, not an explicit integration

Outbound delivery is explicit (`hermes send`). Inbound gate handling assumes the
orchestrator profile will receive a reply, infer the slug/action, load the right
skill/context, find the repo, and shell to `proposal_actions.py`. There is no
implemented Telegram command parser, webhook, gateway hook, blocked gate card,
or session-correlation mechanism in this repository.

Smallest reliable shape: represent approval as a blocked Kanban gate task and
wire a deterministic gateway command/hook that resolves `approve|shelve|modify
<slug>` to that item, or use an attached/continuable session with explicit
correlation and integration tests.

Evidence: `skills/templates/triage-orchestrator/SKILL.md:71-95`.

### P1 — `kanban_store.py` writes Hermes’ private SQLite schema directly

A compatibility probe against the installed Hermes 0.20.0 schema succeeded for
basic card insertion (`ready` with no parents; `todo` with an open parent). That
proves narrow schema compatibility today, not API compatibility.

Direct writes bypass current Hermes behavior such as:

- parent/task validation and cycle checks;
- idempotency keys;
- notification subscription inheritance;
- current run records and structured handoffs;
- canonical completion/promotion and attachment preservation;
- model/skill/runtime/retry limits;
- migrations and future schema changes.

Smallest fix shape: make the deterministic engine emit pure specs, then apply
them through documented `hermes kanban ... --json` calls or a supported Kanban
adapter. Do not maintain a parallel subset of Hermes’ lifecycle code.

Evidence: `engine/kanban_store.py:1-94` and the official Kanban documentation.

### P1 — Several advertised mechanisms are placeholders

- `dedup.method: embedding` is accepted but ignored; all calls use token cosine.
- `item_schema` is loaded but does not drive the fixed intake parser.
- `cost_report.py` probes an optional `tasks.cost_usd` column and otherwise returns
  “telemetry unavailable”; the orchestrator procedure never invokes it.
- `init` and `install` are stubs.
- `scaffold` prints a plan but performs no checks or mutations.

Evidence: `engine/engine.py:73-79`, `engine/config.py:194-203`,
`engine/intake_parser.py:29-99`, `scripts/cost_report.py:39-78`,
`cli/triage.py:4-19`.

### P1 — No quality-repair loop

Dispatcher retry handles crashes/spawn failures. It does not encode review
feedback. The example has build → test → report, but a failed tester has no
configured correction task, re-verification task, bounded defect budget, or hard
block preventing final report/delivery.

For high-value outputs, use:

```text
producer → independent verifier
                 ├─ pass → release/delivery
                 └─ fail → correction → fresh verifier (bounded) → ...
```

### P1 — Tests prove graph primitives, not operations

The 12 tests cover config references, score validation, route mapping, research
spec shape, persistent fulfillment workspaces, and role mapping. They do not test:

- intake report parsing;
- item vault round trips;
- actual Kanban card creation/promotion;
- `proposal_actions.py` against a disposable real board;
- prep-chain wiring;
- human reply routing;
- profile dispatch;
- cron execution;
- outbound delivery;
- crash/retry/repair behavior;
- a complete seed-to-local-artifact run.

Evidence: `tests/test_engine_core.py:58-140`.

## 10. Recommended target architecture

Preserve the repository’s good abstraction but tighten the execution boundary:

```text
Domain config + templates
          │
          v
Validated deterministic planner
  - emits complete DAG
  - absolute artifact contracts
  - idempotency/runtime/retry policy
          │
          v
Canonical Hermes Kanban adapter
  - create/link/complete/block via supported API
  - structured task/run handoffs
          │
          v
Named Hermes profiles
  - judgment/research/build/review only
          │
          v
Explicit blocked human gate
  - deterministic reply correlation
          │
          v
Persistent fulfillment + independent verification
          │
          v
Authorized terminal outcome
  - local artifact, branch, PR, message, or publication
```

### Priority order

1. **Choose the real terminal outcome.** For example: reviewed local artifact,
   committed branch, draft PR, or delivered report. “Deliver” is too vague.
2. **Fix workspace and path contracts.** Every worker must know exactly where the
   repo, item, parent evidence, and output live.
3. **Move complete DAG application into deterministic code.** Include root,
   fan-out, route fan-in, prep, proposal, gate, fulfill, verify, and delivery.
4. **Replace direct board writes with canonical Hermes APIs.** Add idempotency,
   runtime limits, retries, skills, and structured handoffs.
5. **Make the human gate a first-class blocked task with deterministic reply
   correlation.** Do not depend on a fresh model session remembering context.
6. **Update scaffolding for Hermes 0.20.0 and make it verify profiles, aliases,
   toolsets, skills, cron ownership, board metadata, and delivery targets.**
7. **Add integration and end-to-end tests before enabling autonomous cron.**
8. **Add correction semantics and independent final verification.**
9. **Only then adapt the domain and enable one scout at a time.**

## 11. How to adapt it systematically

Before editing, answer these six questions:

1. What exactly is one item?
2. Where can it be detected reliably?
3. What evidence makes it worth spending more effort on?
4. What research must finish before routing?
5. What terminal artifact does each route produce?
6. What exact decision does the human approve?

Then adapt in this order:

1. Rewrite pipeline identity, board, workspace, and terminal outcome.
2. Define source contracts and evidence requirements.
3. Define item schema and make the parser/schema validation real.
4. Define a conservative rubric with anchored scoring examples.
5. Define independent research lanes and one typed classifier output.
6. Define route values exhaustively.
7. Define paths, deliverables, rails, verifier, and repair behavior.
8. Map roles to the smallest reasonable profile fleet.
9. Define one explicit blocked human gate.
10. Generate a complete DAG and inspect it before applying side effects.
11. Run config/unit tests.
12. Run a disposable real-board integration test.
13. Run a tiny end-to-end seed to a local artifact with publishing disabled.
14. Enable one source cron; observe a full cycle; then add sources.

For the user’s roadmap-generation use case, a likely topology is:

```text
seed topic
   │
   v
planner emits structured research plan
   │
   ├─ official-docs researcher ──────┐
   ├─ implementation/examples ───────┤
   ├─ alternatives/tradeoffs ────────┤
   └─ reviewer/cross-reference ──────┘
                  │
                  v
          evidence synthesizer
                  │
                  v
          content generator
                  │
                  v
      independent content/repo review
            │ fail        │ pass
            v             v
         correction    human gate
                            │
                            v
                   commit + reviewed PR
```

That use case changes more than the topic: it adds dynamic fan-out, repository
worktree/integration mechanics, correction loops, and a PR terminal outcome.
Those are mechanisms and therefore justify engine extensions rather than only a
`triage.yaml` rewrite.

## 12. Verification performed for this assessment

Repository revision:

```text
fa4a9dea2bbf86c89ef5887af9a05da64d041b4a
main, clean at inspection start, origin:
https://github.com/funsaized/hermes-multi-agent-workflow.git
```

Runtime inspected:

```text
Hermes Agent v0.20.0 (2026.8.3)
```

Executed in an isolated local `.venv`:

```text
python -m cli.triage validate
[OK] triage.yaml valid - pipeline 'ai-agent-pain-points'

python -m unittest discover -s tests
Ran 12 tests in 0.001s
OK
```

A disposable-board probe initialized a board with the installed Hermes runtime,
then inserted parent/child tasks through this repository’s `KanbanStore`. The
observed statuses were:

```text
first, no parent       → ready
second, parent=first   → todo
completed root         → done
```

This confirms narrow schema compatibility with Hermes 0.20.0. It does not verify
profile dispatch, cron, proposal delivery, gate reply handling, or a full live
pipeline. Those remain open operational unknowns until an end-to-end smoke test
runs on disposable profiles/board/channel.

## Bottom line

The valuable core is the architecture:

- config-driven domain policy;
- deterministic mechanics separated from model judgment;
- durable Kanban coordination;
- parallel research with fan-in;
- one human gate;
- persistent artifact workspaces;
- role-specific Hermes profiles.

The immediate mistake would be to customize the AI-pain-point example and turn on
cron before fixing the integration boundary. First make one synthetic item travel
through a complete, deterministic Hermes DAG to a verified local artifact. Then
repoint the config to the real domain. That sequence preserves the good design and
prevents debugging domain prompts, board lifecycle, workspaces, profiles, cron,
and messaging all at once.
