# AGENTS.md — read this first

**You are an AI coding agent helping a human adapt this template to their own
work.** This file is your map. Read it fully before you touch anything.

## What this repository is

The **Hermes Multi-Agent Workflow**: a reusable skeleton for an autonomous, multi-agent triage pipeline that

> **detects** items from sources → **dedups** → **scores** them against a rubric →
> **researches** in parallel → **routes** to one of several paths → stops at **one
> human gate** → **fulfills** → **delivers**.

It is a **template, not a finished product.** Out of the box it ships two worked
pipelines: `triage-ai-engineering.yaml` (AI Engineering Skills Map learning
artifacts) and `triage-graph-eng.yaml` (context-graph research for agentic
delivery). The human who cloned this wants to repoint it at *their* domain.
Your job is to help them do that.

## The single most important rule

**The domain lives in the pipeline config (a `triage-*.yaml` file), not in the
Python.** The `engine/` package is generic and should stay that way. When the
human says "make this about X," your default move is to **edit the pipeline
config** and the markdown templates it points at — *not* to edit `engine/`.

You touch `engine/` only to add a new **mechanism** (a new kind of step, a new
scoring mode, an embedding backend). You never edit it to encode a **topic**.

If you find yourself writing the human's subject matter into a `.py` file, stop —
that belongs in config.

## Orientation: what's generic vs. what's theirs

| Generic engine (rarely edit) | The human's domain (edit freely) |
|---|---|
| `engine/config.py` — loads/validates the config | `triage-*.yaml` — the whole pipeline definition |
| `engine/engine.py` — deterministic step logic | `paths/rails/*.md` — what may be built |
| `engine/scoring.py` — applies the rubric | `paths/specs/*.md` — output formats |
| `engine/routing.py` — applies the route map | `paths/proposals/*.md` — gate messages |
| `engine/dedup.py` — similarity | `skills/templates/*/SKILL.md` — scout + orchestrator behavior |
| `engine/item_vault.py`, `kanban_store.py`, `frontmatter.py`, `intake_parser.py` | env: profiles, models, board name, schedules |
| `intake_actions.py` — intake adapter (plan/apply/verify) | per-role/per-stage `model:` routing in `roles:`/stages |
| `pre_gate_actions.py` — route + prep + proposal | each path's `deliverable:` file |
| `proposal_actions.py` — gate handler (config-driven) | |
| `delivery_actions.py` — gate sends (proposal + deliverable, one attachment message each) | |

## Architecture in one paragraph

**Fat engine, thin skill.** Deterministic calculations and task-spec generation
(dedup, scoring math, route resolution, lane/stage specs, workspace selection,
model routing) live in `engine/engine.py::TriageEngine`. Four deterministic
adapters apply every board mutation: `intake_actions.py` (items + the complete
pre-gate graph), `pre_gate_actions.py` (route → prep → proposal, incl. auto
paths), `proposal_actions.py` (post-gate chain on approve), and
`delivery_actions.py` (final send). The model contributes exactly three
judgments: rubric scores, reading the classifier value, and proposal prose. If
a pipeline step seems to need ad-hoc code in a worker session, that is an
adapter gap — fix it in the repo with tests and the synthetic eval, never with
a one-off script. Read `docs/01-architecture.md`.

## How to help the human adapt it (the standard flow)

Follow `docs/04-adapting-to-your-domain.md`. In short:

1. **Interview the human** for: their domain, what their scouts should watch, the
   rubric that decides "worth doing," the route decision, and what each path
   should *produce*.
2. **Rewrite the pipeline config** to match — sources, item_schema, rubric,
   research lanes, route map, paths, roles.
3. **Rewrite the markdown templates** under `paths/` (rails, specs, proposals),
   each `sources[].query`, and any shared behavior in `skills/templates/`.
4. **Validate:** `python -m cli.triage --config <pipeline>.yaml validate` until
   it's clean (or set `TRIAGE_CONFIG` once).
5. **Keep tests green:** `python -m unittest discover -s tests` (includes the
   synthetic end-to-end eval; `python scripts/run_synthetic_eval.py` runs it
   standalone). Add domain cases.
6. **Scaffold:** `python -m cli.triage scaffold` prints the Hermes setup plan
   (board, profiles, toolsets, cron, gateways, and skill-install checkpoints).
   `python -m cli.triage render-skills` separately writes staged skill files.
   Walk the human through both; see `docs/07-runbook.md`.

## Hard-won gotchas baked into this template (do not regress)

These cost real debugging in the system this was extracted from. Preserve them:

- **Cron scouts submit through the scoped helper, which uses the Kanban CLI.** Scouts run via
  cron, so `HERMES_KANBAN_TASK` is unset and model-level `kanban_*` tools are not
  injected. Give the scout `terminal` on the cron surface and require
  `scout_actions.py`; it derives the intake path from config and runs
  `hermes kanban --board <board> create ... --json`. Workers spawned from that
  card receive Kanban tools automatically and should use them instead of CLI.
- **Post-gate stages must use a persistent `dir` workspace, not scratch.** Scratch
  dirs are wiped between tasks, stranding the final delivery step. `engine.py`
  already does this for `fulfill` chains — don't change it to scratch.
- **Setting status ≠ delivering.** Status fields don't notify anyone. Both gate
  sends are enforced by `delivery_actions.py` (`send-proposal` from the propose
  card's body, `deliver` wired into the last fulfillment stage's body) — keep
  both.
- **Never send long content as message text.** `hermes send --file` sends file
  *contents* as text; Discord chunks it into a rapid burst of 2000-char messages
  and rate-limits (a 17 KB proposal killed a live propose card this way). The
  adapter sends ONE message with the file as a `MEDIA:` attachment plus a short
  caption, with bounded 429 backoff. Workers never retry sends in a loop — an
  improvised retry burst trips the platform's *global* bucket.
- **The deliverable belongs at the workspace root.** The delivery adapter
  resolves `paths.<path>.deliverable` against the persistent workspace ROOT
  with a non-recursive glob; a live build stage once nested everything under
  `build/` and blocked delivery. The engine states this contract on every
  fulfillment card and has the FIRST stage verify it with
  `delivery_actions.py deliver <slug> --dry-run` — keep both injections, and
  fix a mismatch by conforming the layout, never by widening the glob.
- **Never hardcode a board DB path.** `engine.kanban_store.resolve_board_db`
  honors `HERMES_KANBAN_DB` / `HERMES_HOME` and probes `~/.hermes` and
  `%LOCALAPPDATA%/hermes` (Windows). Hardcoded per-machine paths in scripts or
  task bodies are how worker sessions historically went off the rails.
- **One slugify.** `engine.item_vault.slugify` is the only slug derivation;
  divergent slugs orphan items and duplicate board graphs.
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
