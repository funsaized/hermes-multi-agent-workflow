# AGENTS.md — read this first

**You are an AI coding agent helping a human adapt this template to their own
work.** This file is your map. Read it fully before you touch anything.

## What this repository is

The **Hermes Multi-Agent Workflow**: a reusable skeleton for an autonomous, multi-agent triage pipeline that

> **detects** items from sources → **dedups** → **scores** them against a rubric →
> **researches** in parallel → **routes** to one of several paths → stops at **one
> human gate** → **fulfills** → **delivers**.

It is a **template, not a finished product.** Out of the box it is wired as a
worked example (finding pain points about AI agents, then building a fix or making
an explainer video). The human who cloned this wants to repoint it at *their*
domain. Your job is to help them do that.

## The single most important rule

**The domain lives in `triage.yaml`, not in the Python.** The `engine/` package
is generic and should stay that way. When the human says "make this about X,"
your default move is to **edit `triage.yaml`** and the markdown templates it
points at — *not* to edit `engine/`.

You touch `engine/` only to add a new **mechanism** (a new kind of step, a new
scoring mode, an embedding backend). You never edit it to encode a **topic**.

If you find yourself writing the human's subject matter into a `.py` file, stop —
that belongs in config.

## Orientation: what's generic vs. what's theirs

| Generic engine (rarely edit) | The human's domain (edit freely) |
|---|---|
| `engine/config.py` — loads/validates triage.yaml | `triage.yaml` — the whole pipeline definition |
| `engine/engine.py` — deterministic step logic | `paths/rails/*.md` — what may be built |
| `engine/scoring.py` — applies the rubric | `paths/specs/*.md` — output formats |
| `engine/routing.py` — applies the route map | `paths/proposals/*.md` — gate messages |
| `engine/dedup.py` — similarity | `skills/templates/*/SKILL.md` — scout + orchestrator behavior |
| `engine/item_vault.py`, `kanban_store.py`, `frontmatter.py`, `intake_parser.py` | env: profiles, models, board name, schedules |
| `proposal_actions.py` — gate handler (config-driven) | |

## Architecture in one paragraph

**Fat engine, thin skill.** Deterministic calculations and task-spec generation
(dedup, scoring math, route resolution, lane/stage specs, workspace selection)
live in `engine/engine.py::TriageEngine`. Deterministic adapters apply and link
prep and fulfillment specs. Pre-gate route-card creation and root completion still
live in orchestrator prose; do not describe those transitions as engine-enforced.
Read `docs/01-architecture.md`.

## How to help the human adapt it (the standard flow)

Follow `docs/04-adapting-to-your-domain.md`. In short:

1. **Interview the human** for: their domain, what their scouts should watch, the
   rubric that decides "worth doing," the route decision, and what each path
   should *produce*.
2. **Rewrite `triage.yaml`** to match — sources, item_schema, rubric, research
   lanes, route map, paths, roles.
3. **Rewrite the markdown templates** under `paths/` (rails, specs, proposals),
   each `sources[].query`, and any shared behavior in `skills/templates/`.
4. **Validate:** `python -m cli.triage validate` until it's clean.
5. **Keep tests green:** `python -m unittest discover -s tests`. Add domain cases.
6. **Scaffold:** `python -m cli.triage scaffold` prints the Hermes setup plan
   (board, profiles, toolsets, cron, gateways, and skill-install checkpoints).
   `python -m cli.triage render-skills` separately writes staged skill files.
   Walk the human through both; see `docs/07-runbook.md`.

## Hard-won gotchas baked into this template (do not regress)

These cost real debugging in the system this was extracted from. Preserve them:

- **Cron scouts create the first card through the Kanban CLI.** Scouts run via
  cron, so `HERMES_KANBAN_TASK` is unset and model-level `kanban_*` tools are not
  injected. Give the scout `terminal` on the cron surface and use
  `hermes kanban --board <board> create ... --json`. Workers spawned from that
  card receive Kanban tools automatically and should use them instead of CLI.
- **Post-gate stages must use a persistent `dir` workspace, not scratch.** Scratch
  dirs are wiped between tasks, stranding the final delivery step. `engine.py`
  already does this for `fulfill` chains — don't change it to scratch.
- **Setting status ≠ delivering.** The orchestrator is a headless worker; it must
  actually run `hermes send --to <gate.target>` to reach the human. Status fields
  don't notify anyone.
- **Gate replies are ordinary text.** Use `approve <slug>`, not `/approve`;
  Hermes reserves `/approve` for command-execution approval.
- **First task in a post-gate chain must be `ready` (no blocking parent).** A
  parent edge would make fulfillment depend unnecessarily on pre-gate task state.

`docs/05-pipeline-stages.md` explains each in context.

## Safety / publishing (the human cares about this)

This template runs LLM-authored code (the build path) and shells out, behind one
human gate. The **scope rails** (`paths/rails/*.md`) are model-visible policy, not
a sandbox; keep them tight and enforce least privilege separately. Before the
human publishes their adapted version, do a security
pass and make sure **no secrets ship**: never commit `.env`, `auth.json`, board
`*.db`, or the `work/`/vault contents. The `.gitignore` covers these — verify it.
Read `docs/06-security.md`.

## Don't

- Don't bake the domain into `engine/`.
- Don't remove the human gate or make it auto-approve.
- Don't loosen the scope rails to fit an idea — shelve or re-route instead.
- Don't commit secrets or real data.
- Don't assume this runs as-is. It's a skeleton; the human's environment (Hermes
  install, profiles, auth, web-search keys) must be set up — see the runbook.
