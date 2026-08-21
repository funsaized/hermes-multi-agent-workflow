---
name: {{SKILL_NAME}}
description: >
  The pipeline driver for the Hermes Multi-Agent Workflow. Triggered by new `intake`
  tasks on the triage board. Dedups, scores, fans out research, routes, proposes
  at the human gate, and on approval delegates fulfillment-chain creation to the
  handler. It calls engine/* for deterministic calculations/spec generation and
  applies the research/root/route board edges that still require orchestration.
metadata:
  hermes:
    tags: [triage, orchestrator]
---

# Triage orchestrator (thin driver)

> **Design contract:** fat engine, thin skill. Anything deterministic —
> dedup lookup, applying the score threshold, route resolution, generating lane
> and stage specs, choosing workspaces — is a call into `engine/`. You still apply
> research/root/route edges to the board; deterministic adapters apply and link
> prep and fulfillment specs. Do not invent a pipeline shape;
> it lives in `triage.yaml`. Read
> `docs/01-architecture.md` and `docs/05-pipeline-stages.md`.

This skill owns pipeline `{{PIPELINE_ID}}`. All commands below run from
`{{PROJECT_ROOT}}`, load `{{CONFIG_PATH}}` explicitly, and operate on board
`{{BOARD}}`. `TRIAGE_VAULT_DIR` and `HERMES_KANBAN_DB` are honored.

This is a dispatcher-spawned worker: use the injected `kanban_create`,
`kanban_show`, `kanban_list`, `kanban_link`, and `kanban_complete` tools. Never
shell to `hermes kanban`, query `kanban.db`, or create probe/test cards. For
fan-in, pass every required parent id in one `kanban_create` call as the `parents` array.

## Trigger

A new `intake` task assigned to you appears on the triage board. Its body is a
path to a scout report.

## Task modes (dispatch safety)

- `intake:` — run steps 1–4 and build the complete pre-research graph.
- `triage:` — this is a release barrier, not a second orchestrator pass. Verify
  that its five evidence lanes, classifier fan-in, and route card already exist, create
  nothing, then complete this current task to release the lanes.
- `route:` — run steps 5–6 only. Never repeat intake, scoring, or fan-out.

Do not infer a different mode. In particular, a `triage:` worker must never
create research cards; doing so races the `intake:` worker and duplicates work.

## Procedure

### 1. Parse intake
Read the report file. Parse it into candidates (`engine/intake_parser.py` shape).

### 2. Dedup (deterministic — call the engine)
For each candidate, ask the engine for similar existing items:
```
python -c "from engine.config import TriageConfig; from engine.engine import TriageEngine; \
import json,sys; e=TriageEngine(TriageConfig.load(r'{{CONFIG_PATH}}')); \
print(json.dumps([m.__dict__ for m in e.dedup(sys.argv[1])]))" "<candidate title + claim>"
```
- `duplicate` → append the new source to the existing item, stop. Don't re-research.
- `possible` → note it, continue, re-check after research.
- `new` → create a vault item (`ItemVault.create_item`) with `status: triage`.

### 3. Score (judgment + engine validation)
This is YOUR judgment. Get the rubric prompt from the engine
(`TriageEngine.rubric_prompt()`), score each dimension honestly, then hand your
breakdown back to `TriageEngine.score(breakdown)` to apply the maxes + threshold.
Write `score` / `score_breakdown` to the item file regardless of outcome.
- Below threshold → shelve automatically. **Do not bother the human.**
- At/above → continue.

(For a deterministic/offline pass you may instead call
`TriageEngine.score_heuristic(candidate)` — see engine/scoring.py.)

### 4. Research fan-out (engine returns specs; you create the cards)
On an `intake:` task, create one triage root parented to the current intake task.
That parent edge keeps the root in `todo` while you build the graph, so the
gateway cannot dispatch a second orchestrator into a half-built graph. Give its
body the `triage:` release-barrier instructions above. Immediately append that
triage task id to the item's `linked_kanban_tasks` frontmatter and save the item;
this is the durable audit link used by gate actions. Do this before creating any
lane cards.

Create the five evidence cards exactly from
`TriageEngine.research_specs(slug, triage_id)`; they run in parallel under the
triage root. Collect their ids, then call
`TriageEngine.classifier_spec(slug, evidence_ids)` and create that classifier
card with all five evidence ids as parents. Finally create one `route` card
parented only to the classifier. Give every evidence, classifier, and route card
the persistent project workspace `dir:{{PROJECT_ROOT}}`. Do not inherit
`{{SKILL_NAME}}` into research cards; only the route card receives it.
Use one `kanban_create` call with the complete `parents` array for each fan-in;
do not experiment with CLI parent syntax or create temporary cards.

Every create call must use a retry-safe idempotency key scoped to the current
intake task: `{{PIPELINE_ID}}:triage:<intake-id>:<slug>`,
`{{PIPELINE_ID}}:research:<intake-id>:<slug>:<lane>`, and
`{{PIPELINE_ID}}:route:<intake-id>:<slug>`.
Always issue those creates for the current intake and trust Hermes idempotency to
return an existing active same-run card. Archived cards are historical evidence,
never an active graph and never a reason to skip a candidate. Do not inspect the
Kanban SQLite database directly; use the injected `kanban_*` tools.

After every qualifying candidate's triage root, five evidence lanes, classifier, and route card exist,
complete the current `intake:` task. Each triage root then promotes and completes
itself in release-barrier mode, releasing its five evidence lanes together. A worker may
only complete its own task; never try to complete a child task id.

### 5. Route (deterministic — call the engine)
When the route card fires, read the classifier value the classifier lane emitted
(`route.classifier` in triage.yaml). Resolve the path:
`TriageEngine.route(classification)` → a path name. Write `path: <name>` on the
item. If the path is `auto` (e.g. `shelve`), close out — no proposal.

### 6. Prep + propose (engine returns ordered prep specs)
Apply the engine specs through the deterministic adapter:
```
python pre_gate_actions.py --config "{{CONFIG_PATH}}" <slug> --route-task "$HERMES_KANBAN_TASK"
```
Never create prep cards yourself. The adapter resolves each abstract
`TaskSpec.role` through `spec.assignee(config)`, preserves its workspace, creates
the linear parent chain, and makes retries idempotent. When prep finishes, draft
the proposal using the path's
proposal template
(`paths/proposals/<path>.md`), set item `status: awaiting_approval`, and **send it
to the human** — you MUST actually deliver it:
```
hermes send --to {{GATE_TARGET}} --file <proposal.md>
```
Setting status is NOT delivery. (See docs/06 + the runbook.) Then move on to
other items while waiting — the gate is non-blocking.

### 7. Gate (human replies; you shell to the handler)
Map the human's reply verb (see `gate:` in triage.yaml — NO leading slash) to:
```
python proposal_actions.py --config "{{CONFIG_PATH}}" approve     {{PIPELINE_ID}}:<slug>
python proposal_actions.py --config "{{CONFIG_PATH}}" shelve      {{PIPELINE_ID}}:<slug> --reason "..."
python proposal_actions.py --config "{{CONFIG_PATH}}" shelve-all  [--except <slug>]
python proposal_actions.py --config "{{CONFIG_PATH}}" modify      {{PIPELINE_ID}}:<slug> --change "..."
```
On `approve`, the handler reads `paths.<path>.fulfill` from triage.yaml and
spawns the post-gate chain in a shared persistent workspace. You do nothing else.

### 8. Deliver
When the final fulfillment stage completes, send the deliverable to the
configured target
(`hermes send --to {{GATE_TARGET}} --file <deliverable>`).

## Rules

- Narrate one line per decision to the configured gate target so the human has a pulse.
- Never auto-approve. The gate is real.
- Only an `intake:` orchestrator writes vault items and creates the research
  graph. `triage:`, `route:`, and lane workers never repeat that fan-out.
- Be honest in scoring/classification — gaming them wastes the human's one tap
  and produces low-value output.
- If you hit a missing tool or ambiguous state, block the task with a reason
  rather than guessing.
