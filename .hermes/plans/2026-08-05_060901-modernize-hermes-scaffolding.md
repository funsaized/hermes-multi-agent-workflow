# Modern Hermes Scaffolding Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Replace the stale, advisory setup output with a version-aware, testable Hermes 0.20+ deployment planner and preflight workflow that creates the right profiles, board, toolsets, profile-local cron jobs, and gateway topology without hand-editing Hermes internals.

**Architecture:** Keep `scaffold` side-effect-free, but move command generation into a pure planner that emits structured deployment steps before rendering shell commands. Add a read-only `preflight` command that checks the installed Hermes CLI and existing resources. Use official profile, config, tools, cron, Kanban, and gateway commands; use one orchestrator gateway as the Kanban dispatcher and one gateway per cron-owning scout profile with Kanban dispatch disabled.

**Tech Stack:** Python stdlib, existing PyYAML config loader, `argparse`, `subprocess`, `unittest`, Hermes Agent CLI >= 0.20.0.

---

## Scope and decision

This plan addresses finding #2 from `docs/08-end-to-end-system-guide.md`: the setup plan is stale relative to modern Hermes.

It includes one adjacent correction because it is inseparable from setup correctness: every profile and scheduled scout must have an explicit absolute project working directory.

It does **not** implement the pipeline DAG, gate handler, canonical Kanban adapter, or end-to-end runtime. Those remain separate findings.

### Target runtime contract

- Minimum supported Hermes: `0.20.0`.
- `scaffold` remains dry-run and makes no external changes.
- `preflight` is read-only.
- `install` remains disabled until the generated plan is covered by disposable-home integration tests.
- Profiles are targeted with `hermes -p <profile> ...`, not assumed wrapper aliases.
- Profile creation uses `--clone-from`, not the removed `--from` form.
- Cron jobs are created in the scout profile’s own cron store with `hermes -p <scout> cron create ...`.
- Cron jobs use `--workdir <absolute-repo-root>` and a self-contained prompt.
- Each cron-owning scout profile runs a profile-local gateway.
- Scout gateways set `kanban.dispatch_in_gateway=false`; only the orchestrator gateway dispatches Kanban work.
- Toolsets are changed through `hermes tools enable`, never by editing `config.yaml` directly.
- Settings are changed through `hermes config set`, never by editing `config.yaml` directly.
- Skills are not silently copied into live profile directories by `scaffold`.

### Intended gateway topology

```text
orchestrator gateway
  ├─ messaging / human gate
  ├─ Kanban dispatcher: ON
  └─ optional orchestrator-local cron

xresearch gateway
  ├─ profile-local scout cron
  └─ Kanban dispatcher: OFF

webresearch gateway
  ├─ profile-local scout cron
  └─ Kanban dispatcher: OFF

Kanban dispatcher spawns all researcher/analyst/builder/tester workers.
Those worker profiles do not need their own gateways unless they later own cron
or messaging channels.
```

This avoids relying on the less-visible `gateway.multiplex_profiles` implementation and follows the public profile model: cron stores and gateways are profile-local.

## Proposed configuration additions

Extend `triage.yaml` with deployment metadata rather than hardcoding it in `cli/triage.py`:

```yaml
hermes:
  min_version: "0.20.0"
  base_profile: default
  gateway_profile: orchestrator
  project_root: .
  profile_strategy: clone
  profiles:
    orchestrator:
      description: "Owns triage state, routes work, and handles the human gate."
      toolsets: [file, terminal, kanban]
    researcher:
      description: "Performs source-backed research lanes and structured handoffs."
      toolsets: [web, file]
    analyst:
      description: "Synthesizes research and develops bounded proposals."
      toolsets: [web, file]
    builder:
      description: "Builds only approved artifacts within path scope rails."
      toolsets: [file, terminal, coding]
    tester:
      description: "Independently tests approved artifacts and reports evidence."
      toolsets: [file, terminal]
    video_producer:
      description: "Produces source-backed slides and scripts."
      toolsets: [web, file]
    xresearch:
      description: "Scheduled source scout for X."
      toolsets: [x_search, file, kanban]
      owns_cron: true
    webresearch:
      description: "Scheduled scout for web, Reddit, and YouTube."
      toolsets: [web, file, kanban]
      owns_cron: true
```

Do not encode model names or credentials in this repository. The generated plan should stop at explicit profile setup/model/auth checkpoints.

## Task 1: Add typed Hermes deployment configuration

**Objective:** Give the scaffolder enough validated data to generate modern setup steps without guessing.

**Files:**
- Modify: `engine/config.py:40-128`
- Modify: `engine/config.py:145-241`
- Modify: `triage.yaml`
- Modify: `docs/03-config-reference.md`
- Test: `tests/test_scaffold.py`

**Steps:**

1. Write failing tests for parsing `hermes.min_version`, `base_profile`, `gateway_profile`, `project_root`, and per-profile descriptions/toolsets/`owns_cron`.
2. Add validation failures for:
   - gateway profile not present in `roles`/deployment profiles;
   - source profile missing from `hermes.profiles`;
   - `owns_cron` profile with no corresponding source;
   - empty profile description;
   - relative project root that cannot resolve from the config file location;
   - unsupported `profile_strategy`.
3. Resolve `project_root` relative to the directory containing the selected config, not process CWD.
4. Keep backward compatibility temporarily: if `hermes:` is absent, derive profiles from `roles`/`sources`, but emit a validation warning that descriptions and toolsets are unspecified.
5. Run:
   - `.venv/bin/python -m unittest tests.test_scaffold -v`
   - `.venv/bin/python -m cli.triage validate`
6. Expected: new tests pass; the worked example validates with explicit deployment metadata.

**Commit:** `feat: add Hermes deployment configuration`

## Task 2: Build a pure deployment-plan model

**Objective:** Separate deployment decisions from text printing so commands can be unit-tested structurally.

**Files:**
- Create: `engine/scaffold.py`
- Modify: `engine/__init__.py`
- Test: `tests/test_scaffold.py`

**Design:**

Add dataclasses such as:

```python
@dataclass(frozen=True)
class CommandStep:
    phase: str
    description: str
    argv: tuple[str, ...]
    profile: str | None = None
    mutates: bool = True
    requires_human: bool = False

@dataclass(frozen=True)
class ManualCheckpoint:
    phase: str
    description: str
    verification: tuple[str, ...]
```

The planner should return data, not execute `subprocess`.

**Steps:**

1. Write failing tests for the exact ordered phases:
   - runtime preflight;
   - board;
   - profiles;
   - profile working directories;
   - toolsets;
   - skills;
   - model/auth checkpoints;
   - cron;
   - gateways;
   - smoke verification.
2. Implement one board command:
   - `hermes kanban boards create <board> --name <name> --description <description> --default-workdir <absolute-root>`.
3. Implement profile creation commands:
   - `hermes profile create <name> --clone-from <base> --description <description>`.
4. Implement profile configuration commands:
   - `hermes -p <name> config set terminal.cwd <absolute-root>`.
5. Implement toolset commands per configured platform:
   - `hermes -p <name> tools enable <toolsets...> --platform cli`.
   - Include an explicit preflight check for the platform used by cron before emitting a cron toolset command; do not guess the platform name.
6. Implement dispatcher topology commands:
   - orchestrator: `hermes -p <orchestrator> config set kanban.dispatch_in_gateway true`;
   - cron-owning scouts: `hermes -p <scout> config set kanban.dispatch_in_gateway false`.
7. Implement profile-local cron commands:
   - `hermes -p <scout> cron create <schedule> <self-contained-prompt> --name <pipeline>-<source>-scout --skill <skill> --workdir <absolute-root> --deliver local`.
8. Implement gateway commands only for orchestrator and cron-owning profiles:
   - `hermes -p <profile> gateway install --start-now --start-on-login`.
9. Include manual checkpoints for model/provider auth and gate-channel auth rather than inventing credentials or provider commands.
10. Run `tests.test_scaffold` and verify exact `argv` tuples, not string fragments.

**Commit:** `feat: generate structured Hermes setup plan`

## Task 3: Add safe shell rendering

**Objective:** Render reviewable commands without quoting bugs or implying that placeholders are executable.

**Files:**
- Modify: `engine/scaffold.py`
- Modify: `cli/triage.py:57-82`
- Test: `tests/test_scaffold.py`

**Steps:**

1. Write failing tests containing spaces in the repository path, descriptions, cron prompts, and schedules.
2. Render each `argv` with `shlex.join()`.
3. Print manual checkpoints as comments, never pseudo-commands containing `<placeholder>`.
4. Add `scaffold --format shell|json`; default to `shell`.
5. JSON output should include phase, description, argv, profile, mutability, and human-checkpoint fields.
6. Remove all stale TODO commands from `cmd_scaffold()`.
7. Verify:
   - `.venv/bin/python -m cli.triage scaffold`
   - `.venv/bin/python -m cli.triage scaffold --format json | .venv/bin/python -m json.tool`
8. Expected: output contains no `--from`, no cron `--profile`, no direct `config.yaml` edits, and no ambiguous wrapper-only commands.

**Commit:** `feat: render modern Hermes scaffold plan`

## Task 4: Add a read-only Hermes preflight command

**Objective:** Refuse to generate an apparently runnable plan when the installed Hermes does not support required capabilities.

**Files:**
- Create: `engine/hermes_preflight.py`
- Modify: `cli/triage.py:93-102`
- Test: `tests/test_preflight.py`

**Checks:**

- `hermes` exists on PATH;
- semantic version is at least `hermes.min_version`;
- required command surfaces exist:
  - `profile create --clone-from --description`;
  - global `-p/--profile`;
  - `kanban boards create --default-workdir`;
  - `tools enable --platform`;
  - `cron create --skill --workdir --deliver --name`;
  - `gateway install --start-now --start-on-login`;
- configured board existence;
- configured profile existence and descriptions;
- skill existence in each target profile;
- required toolsets enabled for the relevant execution surface;
- orchestrator and scout gateway status;
- cron status and named job presence;
- gate delivery target availability via `hermes -p <orchestrator> send --list <channel>`.

**Steps:**

1. Write tests using a fake command runner; do not depend on the developer’s real Hermes installation.
2. Return a structured `PreflightReport` with `ok`, `errors`, `warnings`, and per-check evidence.
3. Add `python -m cli.triage preflight --format text|json`.
4. Make `scaffold` print preflight warnings but remain dry-run.
5. Never print `.env`, auth contents, bot IDs, or tokens.
6. Verify mocked old/new Hermes cases and one read-only live run.

**Commit:** `feat: add Hermes deployment preflight`

## Task 5: Define skill materialization explicitly

**Objective:** Remove “copy this folder somewhere” ambiguity without silently mutating live profiles.

**Files:**
- Create: `cli/render_skills.py` or add a `render-skills` subcommand to `cli/triage.py`
- Modify: `skills/templates/triage-scout/SKILL.md`
- Modify: `skills/templates/triage-orchestrator/SKILL.md`
- Test: `tests/test_render_skills.py`

**Steps:**

1. Write failing tests that render one source-specific scout per `sources[]` entry.
2. Materialize skills into a repository-local generated directory such as `work/scaffold/profiles/<profile>/skills/<skill>/SKILL.md`.
3. Replace source ID, skill name, board, query, absolute intake directory, and absolute project root deterministically.
4. Validate rendered `SKILL.md` frontmatter and ensure no TODO placeholders remain.
5. Keep installation a reviewed manual step in this phase.
6. Emit exact destination information from `scaffold`; do not directly patch another Hermes profile.
7. Document two supported installation paths:
   - reviewed local copy into the target profile; or
   - package the rendered profile as a Hermes profile distribution.
8. Defer automatic `hermes profile install <local-distribution>` until profile distributions are generated and tested as a separate feature.

**Commit:** `feat: render profile-specific triage skills`

## Task 6: Modernize the runbook and architecture docs

**Objective:** Make the documented setup flow match the generated and tested plan.

**Files:**
- Modify: `README.md:38-57`
- Modify: `docs/03-config-reference.md`
- Modify: `docs/04-adapting-to-your-domain.md:84-88`
- Modify: `docs/05-pipeline-stages.md:6-16`
- Rewrite relevant sections: `docs/07-runbook.md`
- Update finding status: `docs/08-end-to-end-system-guide.md:499-514`

**Required documentation changes:**

- State Hermes `>=0.20.0` support.
- Use `hermes -p <profile>` consistently; aliases are optional convenience only.
- Replace `--from` with `--clone-from`.
- Explain profile-local cron ownership.
- Explain why scout profiles need their own gateway in the supported topology.
- Explain why scout gateways disable Kanban dispatch.
- Replace hand-edited config instructions with `hermes config set` and `hermes tools enable`.
- Show `--workdir` on every scout cron.
- Explain `--deliver local` for scout jobs: the job’s purpose is to create intake cards, not send a cron response.
- Add `hermes cron runs <job>` and `hermes gateway list` to troubleshooting.
- Separate “config validates” from “Hermes resources are ready” (`preflight`).
- Keep publishing/credentials approval boundaries explicit.

**Verification:** run a repository search and require zero stale patterns:

```text
profile create .* --from
cron create .* --profile
edit .*config.yaml
TODO: confirm subcommand
TODO confirm flags
```

**Commit:** `docs: update setup for Hermes 0.20`

## Task 7: Add CLI contract tests against the installed Hermes help surface

**Objective:** Detect future Hermes CLI drift before shipping another stale scaffolder.

**Files:**
- Create: `tests/test_hermes_cli_contract.py`
- Modify: `README.md` or `CONTRIBUTING.md`

**Steps:**

1. Mark the contract tests as opt-in when Hermes is unavailable.
2. Parse only stable help capabilities, not cosmetic formatting.
3. Check every generated subcommand/flag against live `--help` output.
4. Run in CI when Hermes is installed; otherwise skip with an explicit reason.
5. Keep pure unit tests authoritative for planner behavior.
6. Add a documented compatibility-update procedure for a new Hermes release.

**Commit:** `test: detect Hermes CLI scaffold drift`

## Task 8: Run a disposable-home setup rehearsal

**Objective:** Prove the generated plan against isolated Hermes state before allowing an `install` command.

**Files:**
- Create: `tests/integration/test_scaffold_disposable_home.py`
- Possibly create: `scripts/rehearse_scaffold.py`

**Safety:**

- Use a temporary `HERMES_HOME`.
- Do not start real messaging gateways.
- Do not read or copy the user’s real `.env` or `auth.json`.
- Use test profiles, board, local cron delivery, and no real provider calls.

**Steps:**

1. Create a disposable Hermes home.
2. Create the board and profiles from generated commands.
3. Verify profile descriptions and `terminal.cwd`.
4. Verify dispatcher flags: orchestrator true, scouts false.
5. Render/install fixture skills into disposable profiles.
6. Create paused or far-future profile-local scout cron jobs.
7. Verify each job appears only under its owning scout profile.
8. Verify `preflight --format json` reports all non-auth resources ready.
9. Remove the disposable environment automatically after the test.
10. Keep actual gateway/provider execution for a later smoke test requiring explicit user approval.

**Commit:** `test: rehearse scaffold in disposable Hermes home`

## Task 9: Final quality gate

**Objective:** Confirm the modernization is complete without expanding scope into runtime DAG work.

**Commands:**

```bash
.venv/bin/python -m cli.triage validate
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m cli.triage preflight --format json
.venv/bin/python -m cli.triage scaffold --format shell
.venv/bin/python -m cli.triage scaffold --format json

git diff --check
git status --short
```

**Acceptance criteria:**

- All unit tests pass.
- Disposable-home integration test passes when enabled.
- Scaffold output contains only commands supported by Hermes >=0.20.0.
- No direct Hermes config-file edits are instructed.
- Cron jobs are profile-local and have an absolute workdir.
- Exactly one supported Kanban dispatcher is configured.
- Only orchestrator and cron-owning scouts require gateways.
- No setup command depends on generated aliases.
- No secrets or real profile data are read or written by tests.
- `install` remains a stub until the user separately approves making setup mutating.

**Commit:** `chore: complete Hermes 0.20 scaffold modernization`

## Risks and tradeoffs

1. **Multiple profile gateways:** This is more processes than undocumented multiplexing, but it uses the public profile model and gives each profile’s cron store an owner. Disable scout dispatchers to avoid competing Kanban dispatcher loops.
2. **Toolset execution surface:** Hermes tool configuration is platform-sensitive. The implementation must discover and test the surface cron sessions use rather than assuming `cli` covers cron.
3. **Cloning profiles:** `--clone-from` is convenient but may inherit broader skills/config than desired. Keep a later migration path to versioned profile distributions for tighter least privilege.
4. **Profile distributions:** They are the clean long-term packaging mechanism, but generating and updating seven or eight distributions is larger than fixing stale scaffold output. Do not block the immediate correctness patch on this.
5. **Gateway service installation:** This is a meaningful external side effect. The planner may print it; an eventual installer must require explicit approval before running it.
6. **Board default workdir:** This improves setup but does not replace explicit task workspace/path contracts. Finding #1 still needs its own implementation.
7. **Version drift:** Pinning only `>=0.20.0` is insufficient without help-surface contract tests; both are required.

## Open questions to resolve during implementation

- Which Hermes tool platform key is authoritative for cron-run agent sessions on 0.20.0? Confirm from source/tests before generating `tools enable --platform ...`.
- Should the orchestrator profile be the base profile clone source, or should `base_profile` always be an explicit user input?
- Should profile descriptions/toolsets live entirely in `triage.yaml`, or should reusable role defaults live in a separate deployment file? Recommendation: keep them in `triage.yaml` until more than one deployment target exists.
- Should generated skills evolve into full local profile distributions? Recommendation: yes, after the dry-run planner and disposable-home rehearsal are stable.

## Definition of done

Finding #2 can be marked resolved when a clean machine with Hermes >=0.20.0 can:

1. run `validate`;
2. run `preflight` and see precise missing prerequisites;
3. render profile-specific skills;
4. review a scaffold plan containing only valid modern commands;
5. execute that plan in a disposable Hermes home;
6. observe the board, profiles, toolsets, profile-local cron ownership, and single-dispatcher gateway topology exactly as documented.

Actual autonomous scouting and the full item lifecycle are not part of this definition; they belong to the later end-to-end runtime milestone.
