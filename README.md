# Hermes Multi-Agent Workflow

A reusable skeleton for an intended **autonomous, multi-agent triage pipeline** built on
[Hermes](https://github.com/NousResearch/hermes-agent): a fleet of agents that
**detects** items from sources, **dedups** them, **scores** them against a rubric,
**researches** them in parallel, **routes** each to a fulfillment path, pauses at
**one human approval gate**, then **fulfills and delivers** — all coordinated on a
single Hermes Kanban board.

It includes two domain configurations: `triage.yaml`, the Andrew Ng AI
Engineering Skills Map example, and `triage-graph-eng.yaml`, which researches
context graphs for agentic enterprise delivery with GitHub Copilot and produces
tutorials, worked examples, labs, or reference packages. The engine remains
generic; each pipeline's subject matter stays in configuration and Markdown.

> **This is a template, not a turnkey app.** It runs its unit tests, its
> synthetic end-to-end eval, and config validation out of the box, but going
> live requires setting up your Hermes install, profiles, auth, and scouts (see
> `docs/07-runbook.md`). The point is to give you — and your coding agent — a
> clear, working structure to adapt. The deterministic spine is end-to-end:
> intake graph construction (`intake_actions.py`), routing/prep
> (`pre_gate_actions.py`), the gate handler (`proposal_actions.py`), and final
> delivery (`delivery_actions.py`) are all adapter-applied. Inbound gate-reply
> correlation is the one remaining orchestrator-skill responsibility.

## The idea

```
sources → intake → dedup → score → research (parallel) → route
                                                            │
              ┌──────────────────────┬──────────────────────┤
            path A                 path B                  shelve
           (prep)                 (prep)                  (auto)
              └──────────┬───────────┘
                   ── HUMAN GATE ──   approve · shelve · modify
              ┌──────────┴───────────┐
            fulfill                fulfill
              └──────────┬───────────┘
                      deliver
```

The shape is fixed; **what flows through it is yours.** Everything domain-specific
lives in one file, `triage.yaml`.

## Quickstart

This template targets **Hermes `>=0.20.0`**. Validate and inspect it before
touching any Hermes install. Use an isolated Python environment; do not install
packages into macOS's system Python. On a modern Mac, install
[uv](https://docs.astral.sh/uv/) with `brew install uv` if it is not already on
your `PATH`, then run:

```bash
uv venv --python 3.11
source .venv/bin/activate
uv pip install -r requirements.txt       # just PyYAML
python -m cli.triage validate            # check the example config
python -m unittest discover -s tests     # full suite incl. the synthetic e2e eval
python scripts/run_synthetic_eval.py     # standalone end-to-end pipeline replay
python -m cli.triage scaffold            # dry-run Hermes setup plan
python -m cli.triage scaffold --no-preflight  # offline/pure-plan rendering
python -m cli.triage preflight           # read-only capability/resource check
python -m cli.triage render-skills       # render profile-specific SKILL.md files
python -m cli.triage --config triage-graph-eng.yaml validate
```

The CLI exposes six surfaces; four are implemented and two are stubs:

| Subcommand        | Mutates Hermes? | Purpose |
|-------------------|-----------------|---------|
| `validate`        | No              | Checks `triage.yaml` consistency. |
| `preflight`       | No              | Confirms the installed Hermes version/flags exist and which configured resources are already present. Exits 1 on blockers. |
| `scaffold`        | No (dry run)    | Renders the deployment plan (shell or JSON) without executing it. Runs read-only preflight by default; use `--no-preflight` for offline rendering. |
| `render-skills`   | Writes local staging only | Renders `work/scaffold/profiles/<profile>/skills/<skill>/SKILL.md` and prints the exact live destination. The base profile uses `$HERMES_HOME/skills/...`; cloned profiles use `$HERMES_HOME/profiles/...`. |
| `init`            | No (stub)       | Prints guidance only; it does not initialize a project. |
| `install`         | No (stub)       | Still a stub. Auto-apply of the scaffold plan is intentionally deferred. |

`scaffold` prints commands; `preflight` verifies the runtime; `render-skills`
produces deterministic files you review and copy yourself. The full live runbook
is in `docs/07-runbook.md`.

## Adapt it to your domain

The whole adaptation is editing `triage.yaml` + the markdown templates it points
at. Hand your coding agent **`AGENTS.md`** and ask it to walk you through
`docs/04-adapting-to-your-domain.md`. In brief:

1. Edit `triage.yaml`: sources, rubric, research lanes, route map, paths
   (including each path's `deliverable:` file), and roles (optionally with
   per-role/per-stage `model:` routing).
2. Edit `paths/` templates (scope rails, deliverable specs, proposal formats).
3. Edit `sources[].query` and the shared skill templates. `render-skills`
   generates one named scout skill per source.
4. `python -m cli.triage validate`, keep `tests/` (which include the synthetic
   end-to-end eval) green.
5. Follow `docs/07-runbook.md` for the Hermes 0.20 setup flow (profile-local cron
   scheduler gateways without Discord, one configured messaging gateway/dispatcher,
   and reviewed local skill copies). `install` is still a stub.

## Repository layout

```
triage.yaml              AI Engineering Skills Map pipeline
triage-graph-eng.yaml    Graph engineering + GitHub Copilot pipeline
AGENTS.md                Guide for the AI agent adapting this template
engine/                  Generic engine (rarely edited)
  config.py              Loads + validates triage.yaml (roles/model routing, deployment metadata)
  scaffold.py            Pure ordered deployment planner + safe shell/JSON rendering
  hermes_preflight.py    Read-only capability + resource check (injectable runner)
  skill_materialization.py Deterministic profile-specific SKILL.md renderer
  engine.py              TriageEngine — deterministic calculations + task specs
  scoring.py             Rubric scoring (LLM mode + deterministic mode)
  routing.py             Classification → path
  dedup.py               Similarity (token-cosine; embedding backend is TODO)
  item_vault.py          One markdown file per tracked item + canonical slugify
  kanban_store.py        Writes the Hermes Kanban board; resolves the Hermes home
intake_actions.py        Deterministic intake adapter — plan / apply (pre-gate graph) / verify
pre_gate_actions.py      Route resolution + prep chain + proposal card (incl. auto paths)
proposal_actions.py      Human-gate handler (approve/shelve/modify) — config-driven
delivery_actions.py      Deterministic gate sends — proposal + deliverable as single attachment messages
scout_actions.py         Config-scoped scout report + intake-card submission
paths/                   Per-path templates you customize
  rails/   specs/   proposals/
skills/templates/        Scout + orchestrator SKILL.md templates
cli/triage.py            validate / scaffold / preflight / render-skills / install-stub
scripts/run_synthetic_eval.py  Synthetic end-to-end pipeline replay (no model, no live Hermes)
scripts/cost_report.py   Standalone per-item spend report (not automatically enforced)
scripts/rehearse_scaffold.py  Opt-in disposable-Home rehearsal (env-flag gated)
tests/                   Generic engine + planner + preflight + render + CLI-contract tests
docs/                    Deep-dive docs (architecture, board, config, adapting, …)
pipelines/graph-eng/     Graph-engineering research guide, rails, and artifacts
examples/                Reference configs
```

## Documentation

- `docs/01-architecture.md` — fat engine / thin skill; how the pieces fit.
- `docs/02-the-board.md` — Kanban as the bus; dispatcher; fan-in.
- `docs/03-config-reference.md` — every `triage.yaml` key.
- `docs/04-adapting-to-your-domain.md` — the step-by-step adaptation guide.
- `docs/05-pipeline-stages.md` — each stage, and the gotchas to preserve.
- `docs/06-security.md` — trust surface, scope rails, safe publishing.
- `docs/07-runbook.md` — profiles, board, crons, go-live.
- `examples/ai-agent-pain-points/REFERENCE.md` — full write-up of the reference
  implementation this template was extracted from.

## Security

This template runs LLM-authored code and shells out, behind one human gate. Read
**`SECURITY.md`** and `docs/06-security.md` before deploying — and run the
pre-publish secret-scan checklist before open-sourcing an adapted copy.

## Contributing

See **`CONTRIBUTING.md`**. The golden rule: keep `engine/` domain-agnostic; new
domains go in `triage.yaml`, not the code.

## License

MIT — see `LICENSE`.

## Credits

Extracted and generalized from a working single-machine Hermes pipeline. The
engine is domain-agnostic; the bundled example reflects its origin.
