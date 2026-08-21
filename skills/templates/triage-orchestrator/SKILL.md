---
name: {{SKILL_NAME}}
description: >
  The pipeline driver for the Hermes Multi-Agent Workflow. Triggered by new `intake`
  tasks on the triage board. Judges rubric scores and reads classifier values; every
  other step — parsing, dedup, item creation, the pre-gate graph, routing, prep,
  fulfillment, and delivery — is applied by the deterministic adapters
  (`intake_actions.py`, `pre_gate_actions.py`, `proposal_actions.py`,
  `delivery_actions.py`).
metadata:
  hermes:
    tags: [triage, orchestrator]
---

# Triage orchestrator (thin driver)

> **Design contract: fat engine, thin skill.** Your judgment is needed in exactly
> three places: scoring rubric dimensions, reading the classifier's value, and
> drafting proposal prose. Everything else is a single adapter command. Never
> write ad-hoc Python, query the Kanban SQLite database, hand-roll slugs or
> idempotency keys, or create probe/test cards. If an adapter is missing a
> capability or fails, **block the task with its error output** — a gap in the
> adapters is a bug to fix in the repo, never something to work around in a
> worker session. Read `docs/01-architecture.md` and `docs/05-pipeline-stages.md`.

This skill owns pipeline `{{PIPELINE_ID}}`. All commands below run from
`{{PROJECT_ROOT}}`, load `{{CONFIG_PATH}}` explicitly, and operate on board
`{{BOARD}}`. `TRIAGE_VAULT_DIR`, `HERMES_KANBAN_DB`, and `HERMES_HOME` are honored.

This is a dispatcher-spawned worker: use the injected `kanban_show`,
`kanban_complete`, `kanban_block`, and `kanban_comment` tools for your OWN task.
Board cards for pipeline steps are created only by the adapters listed here.

## Task modes (dispatch safety)

- `intake:` — steps 1–3: plan, score, apply. The adapter builds the complete
  pre-research graph.
- `triage:` — a release barrier, not a second orchestrator pass. Run the verify
  command in the task body, create nothing, complete yourself.
- `route:` — step 4 only: read the classifier value, run `pre_gate_actions.py`.
  Never repeat intake, scoring, or fan-out.

Do not infer a different mode from context.

## Procedure

### 1. Plan (deterministic)
The intake task body is a path to a scout report. Run:
```
python intake_actions.py --config "{{CONFIG_PATH}}" plan --intake <report-path>
```
It parses candidates, dedups them against the vault, and prints the rubric
prompt plus one entry per candidate with its canonical `slug`.

### 2. Score (YOUR judgment)
For every candidate with `needs_score: true`, score each rubric dimension
honestly using the printed rubric prompt. Write the breakdowns to a scores file
next to the report (`<report>.scores.json`):
```
{"scores": {"<slug>": {"<dimension>": <int>, ...}, ...}}
```
Be conservative — inflating scores to keep weak items alive wastes the human's
one approval tap.

### 3. Apply (deterministic)
```
python intake_actions.py --config "{{CONFIG_PATH}}" apply \
  --intake <report-path> --scores <report>.scores.json --intake-task "$HERMES_KANBAN_TASK"
```
The adapter validates your scores through the engine, merges duplicates, creates
vault items, shelves below-threshold candidates, and for each advancing item
creates the triage root, evidence lanes, classifier fan-in, and route card with
retry-safe idempotency keys. Re-running it is safe. When it returns `ok: true`,
complete the intake task. When it reports problems, fix your scores file or
block with the JSON output.

### 4. Route (read one value, then deterministic)
When a `route:` task fires, read the parent classifier's result and extract the
classifier value named in its body. Then:
```
python pre_gate_actions.py --config "{{CONFIG_PATH}}" <slug> \
  --classification "<value>" --route-task "$HERMES_KANBAN_TASK"
```
The adapter resolves the path via `route.map`, writes it on the item, and either
creates the linked prep chain plus one `propose:` card, or closes out an auto
path (e.g. shelve). Never create prep or proposal cards yourself. Confirm the
JSON response, then complete this route task. The proposal is drafted later by
the dependency-gated proposal worker; the gate remains non-blocking.

### 5. Gate (human replies; you shell to the handler)
Map the human's reply verb (see `gate:` in triage.yaml — NO leading slash) to:
```
python proposal_actions.py --config "{{CONFIG_PATH}}" approve     {{PIPELINE_ID}}:<slug>
python proposal_actions.py --config "{{CONFIG_PATH}}" shelve      {{PIPELINE_ID}}:<slug> --reason "..."
python proposal_actions.py --config "{{CONFIG_PATH}}" shelve-all  [--except <slug>]
python proposal_actions.py --config "{{CONFIG_PATH}}" modify      {{PIPELINE_ID}}:<slug> --change "..."
```
On `approve`, the handler spawns the post-gate chain in a shared persistent
workspace. You do nothing else.

### 6. Deliver (deterministic)
The final fulfillment stage's task body instructs its worker to run
`delivery_actions.py`, which locates the configured deliverable and sends it to
{{GATE_TARGET}} via `hermes send`. You never send deliverables ad hoc; if a
delivery task is blocked, surface the adapter's error to the human.

## Rules

- Narrate one line per decision to the configured gate target so the human has a pulse.
- Never auto-approve. The gate is real.
- Only the `intake:` adapter run writes vault items and creates the research
  graph. `triage:`, `route:`, and lane workers never repeat that fan-out.
- Be honest in scoring/classification — gaming them wastes the human's one tap
  and produces low-value output.
- Adapter output is the source of truth. If a command exits non-zero or state is
  ambiguous, block the task with the JSON error as the reason rather than
  guessing, retrying with hand-rolled SQL, or writing a script.
