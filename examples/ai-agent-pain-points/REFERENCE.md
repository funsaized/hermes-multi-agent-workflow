# Historical reference design — the pain-point pipeline

> This is a **historical design write-up only**, retained to explain the system
> that inspired the template. It is not proof that the current repository runs
> end to end, and its old names, paths, roles, and delivery wording are not
> deployment instructions. `triage.yaml` is the only current configuration.

## What it is

A single-machine, autonomous pipeline that detects pain points AI-agent users hit,
validates them against a rubric, and routes each one to either **building a fix**
or **producing an explainer video** for a confusing existing solution — with one
human approval gate. It runs as a fleet of Hermes profiles on one PC sharing one
Kanban board.

## Why single-machine, one board

An earlier design used three devices coordinating over a bot-to-bot messaging
bus. That proved unreliable. The working design collapses everything onto **one
host, one Kanban board** as the inter-agent bus, and keeps Discord for the
**human gate only**. No cross-device transport, no message queue between agents.

## The fleet (roles → models)

Eight profiles on one install, each bound to a model in its own config:

| Role | Model | Job |
|---|---|---|
| scout (X) | Grok via OAuth | Hourly scrape of X for pain points |
| scout (web) | GPT via OAuth | Hourly scrape of Reddit / YouTube / web |
| default gateway | GPT | Pipeline driver; reuses the existing Discord-facing root profile |
| researcher | GPT | The three research lanes (verify / context / solutions audit) |
| analyst | GPT | Synthesize problem + ideate solutions (build path) |
| builder | GPT | Build the approved prototype |
| tester | GPT | Test the prototype on real inputs |
| video_producer | GPT | Tutorial research, outline, slides, script (video path) |

The point of mixing models: the X scout uses a provider with first-class X access;
everything else uses one general model. The engine doesn't care — the model is
bound per profile, and `roles:` in `triage.yaml` maps roles to profiles.

## The flow

```
scouts (cron, staggered)  →  intake card on the board
        │
   orchestrator: dedup → score (rubric, threshold 65/100)
        │   (< 65 → auto-shelved, human never bothered)
        ▼
   research fan-out (3 parallel lanes; route fan-ins on all 3)
        │
   ROUTE on existing-solutions audit:
     missing / broken         → BUILD
     confusing / poorly-doc'd → VIDEO
     good                     → SHELVE
        │
   prep → PROPOSAL  → ── HUMAN GATE (Discord #briefs) ── approve / shelve / modify
        │
   BUILD:  prototype → test → report          VIDEO: slides → script → deliver
        │
   delivered to the configured human-gate target
```

## The rubric

Five dimensions, 0–100, ship at 65: frequency (25), pain intensity (20),
agent-solvable-or-explainable (25), solution gap (15), strategic fit (15). The
"OR-explainable" dimension is what makes one pipeline able to either build or
explain — it's path-agnostic.

## The two-branch trick

The same rubric, gate, and reply verbs serve two very different outcomes (ship
code vs. ship a teaching video). Only the proposal content and the fulfillment
chain differ. In the engine this is just two entries under `paths:`.

## Why one human gate

The build path is the most expensive, error-prone stage, and agent-tested
agent-code shares blind spots. Gating *before* fulfillment keeps a person in
control of what actually ships.
The current repository does not automatically enforce the configured cost
threshold. Below-threshold items are intended to be dropped
automatically, so the human only ever sees things worth a decision.

## Scope-rail policy (build path)

Acceptable build targets are bounded on purpose: a Hermes skill/plugin, a CLI
tool, a markdown playbook, a small script, or a cron + skill. Explicitly **not**:
full SaaS apps, stateful auth flows, anything needing un-provisioned keys, or real
UI beyond a dashboard plugin. When a proposal doesn't fit, it's shelved or
re-routed — the rails are never widened to fit. These are model-visible policy,
not a technical sandbox.

## Operational lessons carried into the template

These informed the template, though not all are deterministic engine guarantees
(see `docs/05-pipeline-stages.md`):

- Scouts run via cron, not the dispatcher, so they need the `kanban` toolset
  explicitly — otherwise they write a report but can't create the intake card.
- Post-gate stages must share a persistent workspace; scratch dirs are wiped
  between tasks and strand the final delivery step.
- A headless orchestrator must actively send messages — setting a status field
  notifies no one.
- The first post-gate task is created `ready` with no blocking parent so its
  readiness is independent of pre-gate task state.
- Gate replies use ordinary text; `/approve` is reserved for Hermes execution approval.

## What the origin system reportedly demonstrated

The origin system reportedly ran end-to-end: scouts surfaced candidates, the
rubric shelved the weak ones, research and routing ran on the board, a proposal
reached the human, one approval spawned the fulfillment chain, and a finished
deliverable was sent back. That historical report is not a verification result
for this repository.
