# 03 — Config reference (`triage.yaml`)

Every key in `triage.yaml`. The typed view is `engine/config.py`; the validator is
`TriageConfig.validate()` (run via `python -m cli.triage validate`).

## Top level

| Key | Type | Meaning |
|---|---|---|
| `name` | str | Pipeline slug, for logs. |
| `board` | str | Kanban board slug. `default` → `~/.hermes/kanban.db`; else `~/.hermes/kanban/boards/<board>/kanban.db`. |
| `workspace_root` | path | Base for the item vault and per-item persistent workspaces. Relative values are resolved by runtime code from the process working directory; run from `hermes.project_root` or use an absolute path. |
| `cost_gate_usd` | number | Threshold read by `scripts/cost_report.py`. The current pipeline does not invoke that script or enforce pause/notify behavior automatically. |

## `hermes:` — deployment metadata

Typed inputs to the dry-run deployment planner. These settings describe the
Hermes installation without selecting models, providers, or credentials.

| Key | Meaning |
|---|---|
| `min_version` | Minimum compatible Hermes CLI version. Use a semantic version such as `0.20.0`; prerelease/build suffixes such as `0.20.0-rc1` or `0.20.0+build.7` are accepted and compared by their release triplet. |
| `base_profile` | Existing profile cloned when creating deployment profiles. The planner emits `hermes profile create <name> --clone-from <base>`; aliases are not used and are optional convenience only. |
| `gateway_profile` | Profile that owns the human gate and the sole Kanban dispatcher. It must occur in both `roles:` and `hermes.profiles:`. |
| `project_root` | Project working directory. A relative path resolves from the selected config file's directory, never the process working directory. It must resolve to an existing directory. |
| `profile_strategy` | Profile creation strategy. Currently only `clone` is supported. |
| `profiles` | Map of Hermes profile name to the metadata below. Every source profile must be present. |

Each `hermes.profiles.<name>` supports:

| Key | Meaning |
|---|---|
| `description` | Required, non-empty purpose. Used by `profile create --description` and preflight for cloned profiles; the existing base profile's description is not enforced. |
| `toolsets` | Toolsets the scaffold enables on the profile's CLI platform and, when `owns_cron: true`, its cron platform. Preflight separately verifies that each name is advertised by the installed runtime and that it is enabled on every required surface. |
| `owns_cron` | Whether this profile owns scheduled source jobs and therefore needs a profile-local gateway process to tick cron. That scheduler gateway has Kanban dispatch disabled and does not need Discord credentials. It is invalid unless a source uses the profile. |

For temporary backward compatibility, omitting `hermes:` derives profile names
from `roles:` and `sources:`. Validation exposes a warning because descriptions
and toolsets cannot be inferred safely; add explicit metadata before applying the
rendered scaffold plan.

## `sources:` — the scouts

List of detectors. Each runs a scout skill on a profile, on a cron.

| Key | Meaning |
|---|---|
| `id` | Short id (used in report filenames + the intake task title). |
| `profile` | Hermes profile the scout runs under (binds the model). |
| `skill` | Name of the source-specific skill rendered from the shared scout template and manually installed on that profile. |
| `schedule` | Cron expression. Registered in the source profile's local cron store (see runbook). |
| `query` | The domain prompt — what to look for. Pasted into the scout skill. |

Every `sources[].id` must be unique. Each `(profile, skill)` pair must also be
unique because it names one rendered skill destination and scheduled scout
identity; `validate` rejects collisions before files or cron commands can
overwrite one another.

## `item_schema.fields:`

Declarative documentation for the fields a scout emits per candidate. The current
parser is fixed to `title`, `claim`, `sources`, and `why_it_may_matter`; it does
not dynamically consume this list, and `validate` does not enforce the required
parser fields. Keep all three surfaces in sync manually.

## `dedup:`

| Key | Meaning |
|---|---|
| `method` | Reserved backend selector. `token-cosine` is the only implementation currently used; setting another value does not switch `TriageEngine.dedup()`. |
| `duplicate_threshold` | ≥ → treat as a duplicate of an existing item. |
| `possible_threshold` | ≥ → flag as a possible duplicate, continue. |

Token-cosine runs colder than embedding cosine; defaults (0.62/0.40) suit it. For
embeddings, raise toward ~0.85/0.65.

## `rubric:`

| Key | Meaning |
|---|---|
| `threshold` | Advance if total score ≥ this. Must be ≤ sum of dimension maxes (validated). |
| `dimensions[]` | `{key, max, hint}`. `key` is the score field; `max` its ceiling; `hint` guides the orchestrator. |

LLM mode (recommended) adapts to ANY dimensions. The deterministic heuristic in
`scoring.py` only understands the reference keys — see docs/04 if you change them.

## `research_lanes:`

| Key | Meaning |
|---|---|
| `role` | Role the lanes run under (mapped via `roles:`). |
| `lanes[]` | Parallel lane task titles. All must finish before route fires. |
| `classifier_lane` | Which lane emits the value the router reads. Must be one of `lanes`. |

## `route:`

| Key | Meaning |
|---|---|
| `classifier` | Dotted reference to the classifier output, e.g. `<lane>.<field>`. Documentation for the orchestrator; the engine matches on the value string. |
| `map` | `{classification_value: path_name}`. Every target must be a key under `paths:` (validated). |

## `paths:`

A map of path name → definition. A path is one outcome of routing.

| Key | Meaning |
|---|---|
| `prep[]` | Ordered stages BEFORE the gate. Each `{stage, role}`. `prep_specs()` does not link them; the caller must create sequential parent edges. |
| `propose.role` | Who drafts + sends the proposal (usually `orchestrator`). |
| `propose.template` | Markdown proposal template under `paths/proposals/`. |
| `fulfill[]` | Stages AFTER approval. Each `{stage, role}`. Run in a shared persistent workspace. |
| `workspace_subdir` | Bucket under `workspace_root` for this path's per-item dirs (e.g. `builds`). Defaults to the path name. |
| `scope_rails` | Markdown prompt-policy file inlined into each worker task. It guides the model but is not a sandbox or technical enforcement boundary. |
| `deliverable_spec` | Markdown file (under `paths/specs/`) inlined into workers — output format. |
| `auto` | `true` → terminal path, no work (e.g. `shelve`). |

`stage` is the task-title prefix and the conventional name workers key off. `role`
is mapped to a profile.

## `roles:`

Map abstract role → real Hermes profile. Every role used anywhere (research,
prep, fulfill, propose) must appear here (validated). This indirection lets you
rename/merge profiles without touching paths.

## `gate:`

| Key | Meaning |
|---|---|
| `channel` | Messaging platform used for proposals (for example, `discord`). |
| `target` | Optional exact `hermes send --to` target (for example, `discord:1484142557704491119`). Must use the `channel` prefix; when omitted, rendering falls back to the channel string. |
| `approve` / `shelve` / `modify` | Ordinary-text reply verbs the orchestrator maps to `proposal_actions.py` subcommands. Do not use `/approve`; Hermes reserves it for execution approval. |

## Deployment flow (Hermes 0.20)

`hermes:` describes the intended deployment; four CLI surfaces turn it into a
live Hermes install without baking any mutating code into this repository. Run
them in this order:

1. `python -m cli.triage validate` — checks `triage.yaml` consistency. Fails on
   broken routes, undefined roles, unreachable thresholds, or invalid
   `hermes:` topology. Missing path template files are printed as non-fatal CLI
   warnings and are resolved relative to the process working directory. Exposes
   `TriageConfig.validation_warnings` for non-fatal compatibility notices (e.g.
   when `hermes:` is missing entirely).
2. `python -m cli.triage preflight --format text|json` — read-only. Verifies
   the installed Hermes version satisfies `min_version`, that every required
   create/install flag exists on the live CLI (`profile create --clone-from`,
   `kanban boards create --default-workdir`, `tools enable --platform`, etc.),
   and that every configured toolset name is available before checking each
   configured board, profile, description, skill, enabled toolset,
   gateway, cron scheduler/job, and gate channel is present. Exits 1 on
   blockers and prints evidence. Never reads credentials.
3. `python -m cli.triage scaffold --format shell|json` — dry-run deployment
   plan. The underlying planner is pure and the command never mutates Hermes;
   unless `--no-preflight` is supplied, the CLI first runs read-only Hermes
   subprocess checks. It emits an ordered
   sequence of `hermes profile create <profile> --clone-from …` for every profile
   except the already-existing `base_profile`,
   `hermes -p <profile> config set terminal.cwd <abs>`,
   `hermes -p <profile> tools enable … --platform cli` plus `--platform cron`
   for cron-owning profiles, profile-local
   `cron create --workdir <abs> --deliver local`, and
   `gateway install --start-now --start-on-login` for the configured gateway and
   every cron-owning profile. A scout's gateway is a local cron scheduler, not an
   additional Discord listener or Kanban dispatcher.
   When `gateway_profile == base_profile`, the existing root gateway is reused
   and no competing gateway installation is emitted. Skill installation and model/auth are
   emitted as `CHECKPOINT:` comments (or `argv: null` in JSON), never as
   invented commands. `scaffold` runs `preflight` by default and writes a concise
   blocker summary to stderr while still rendering the full plan. Add
   `--no-preflight` to render without inspecting a live Hermes runtime.
4. `python -m cli.triage render-skills` — writes profile-specific scout and
   orchestrator SKILL.md files under
   `work/scaffold/profiles/<profile>/skills/<skill>/SKILL.md`. For each
   rendered file it prints the exact
   `$HERMES_HOME/profiles/<profile>/skills/<skill>/SKILL.md` destination you
   must copy to manually, or the path of the rendered profile tree you would
   package as a Hermes profile distribution. Skills for the base profile target
   `$HERMES_HOME/skills/<skill>/SKILL.md`. It never touches live Hermes
   state.
5. `python -m cli.triage install` — still a stub. Automatic application of
   the scaffold plan is intentionally deferred. Do not invoke it on a live
   Hermes home until a separately approved installer ships.

This split keeps deployment mutations out of these CLI surfaces. Configuration,
planning, preflight, and rendering produce reviewable artifacts; a human applies
the emitted Hermes commands and copies or packages skills. Runtime
`proposal_actions.py` does mutate the selected Kanban database after a gate
decision.

## Validation guarantees

`validate()` fails (with actionable messages) if: a route target isn't a defined
path; a role is used but undefined; the threshold exceeds the max possible total;
the classifier lane isn't in `lanes`; or Hermes deployment metadata/topology is
inconsistent. `validation_warnings` exposes non-fatal compatibility warnings.
The CLI also warns if a referenced template file is missing. It does not validate
cron syntax, dedup backend support/threshold ordering, or `item_schema` against
the fixed intake parser.
