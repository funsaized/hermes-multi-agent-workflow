# 07 — Runbook (stand it up + go live on Hermes 0.20)

This template validates, unit-tests, and renders a deployment plan out of the
box, but going live is still a human-driven sequence of Hermes 0.20 commands.
The engine produces typed metadata, validates it, runs a read-only preflight
against your installed Hermes, renders a dry-run plan, and writes local copies
of profile-specific skills. **The engine itself never mutates your live Hermes
home** — `python -m cli.triage install` is intentionally a stub.

The split:

| Surface                     | What it does                                            | Mutates Hermes? |
|-----------------------------|---------------------------------------------------------|-----------------|
| `validate`                  | Checks `triage.yaml` consistency.                       | No              |
| `preflight`                 | Confirms the installed Hermes runtime + configured resources are ready. Exits 1 on blockers. | No |
| `scaffold`                  | Dry-run deployment plan (shell or JSON).                | No              |
| `render-skills`             | Writes profile-specific `SKILL.md` files under `work/scaffold/profiles/...` and prints the exact live destination for each. | No |
| `install`                   | Stub. Not implemented; deferred.                       | No              |

You execute the scaffolded commands and the manual install yourself.

> Commands assume `hermes` is on `PATH`. Hermes 0.20 uses
> `hermes -p <profile>` to target a profile; aliases are optional convenience
> only. On WSL, run them in your Linux shell.

## 1. Prerequisites

- Hermes `>=0.20.0` installed (`hermes --version`).
- An isolated project environment with PyYAML installed. On macOS, the
  recommended setup is `brew install uv` (if needed), then from this repo:
  `uv venv --python 3.11`, `source .venv/bin/activate`, and
  `uv pip install -r requirements.txt`. Do not install into Apple's system
  Python.
- `python -m cli.triage validate` is clean.
- (Recommended) `python -m cli.triage preflight --format json` exits 0 with no
  capability blockers in your Hermes home. Resource blockers are expected
  before you apply the scaffold.

## 2. Inspect the deployment plan

```bash
python -m cli.triage scaffold --format shell    # review before executing
python -m cli.triage scaffold --format json     # machine-readable plan
python -m cli.triage scaffold --no-preflight    # offline/pure-plan rendering
```

The plan is a fixed sequence of phases: runtime preflight → board → profiles
→ profile working directories → toolsets → skills → model/auth checkpoints
→ cron → gateways → smoke verification. It uses `--clone-from` (not `--from`),
`hermes -p <profile>` for every profile-scoped step, absolute `--workdir` on
every cron job, and emits both CLI toolset commands and cron-surface toolset
commands for cron owners. The opening runtime checkpoint gates execution of
those commands on preflight capability checks. Skill installation and
model/auth remain `CHECKPOINT:` comments (or `argv: null` in JSON), not
invented commands.

The configured `base_profile` is treated as pre-existing: the plan does not
recreate it, overwrite its default working directory, or install a second
gateway for it. This example sets `gateway_profile: default`, reusing the root
Discord gateway as the sole dispatcher and inbound human-gate listener.

By default, `scaffold` first performs the same read-only live inspection as
`preflight` and reports blockers to stderr while still rendering the plan. Use
`--no-preflight` in CI, offline environments, or whenever you only need the
pure configuration-derived plan; it does not weaken the checkpoint that must
pass before a human executes the commands.

Skill installation is NOT automated. The plan prints local staging paths
(`work/scaffold/profiles/<profile>/skills/<skill>/SKILL.md`) and the exact live
destination for each. Cloned profiles use
`$HERMES_HOME/profiles/<profile>/skills/<skill>/SKILL.md`; the base profile uses
`$HERMES_HOME/skills/<skill>/SKILL.md`. The model/auth checkpoints are yours.

## 3. Create the board

```bash
hermes kanban boards create <board> \
    --name <display-name> \
    --description '<board purpose>' \
    --default-workdir <abs-path-to-this-repo>
```

The `--default-workdir` is what `kanban` will suggest as the worker workspace.
Setting it to the repo root keeps workers close to `triage.yaml` and
`engine/`. `--clone-from` does not apply here.

## 4. Create the profiles

One profile per distinct value in `roles:` plus each `sources[].profile`. The
already-existing `hermes.base_profile` is reused. For every other profile, the
scaffold emits `hermes profile create <name> --clone-from <base>
--no-alias --description '<from triage.yaml>'` per profile. `--no-alias` keeps
rehearsals inside their temporary home; remove it on your real machine if you
want per-profile wrapper scripts (`orchestrator`, `xresearch`, …) on PATH.

```bash
hermes profile create <name> --clone-from <base> \
    --no-alias \
    --description '<from triage.yaml>'
```

Each cloned profile's description MUST match `hermes.profiles.<name>.description`
exactly — `preflight` checks it through `profile describe` and fails the run
if the configured description drifts. The base profile's existing description
is intentionally not enforced. Multi-model is fine: a scout can use
one provider, the orchestrator another. The profile is where the model is
bound; the engine doesn't care.

## 5. Pin each cloned profile's working directory

`tools enable` and `cron create` both accept `--workdir`, but per-profile
default state lives in the profile config. To avoid hijacking the user's normal
CLI workspace, the scaffold leaves the base profile's default working directory
alone and emits this only for cloned profiles:

```bash
hermes -p <profile> config set terminal.cwd <abs-path-to-this-repo>
```

Use `hermes -p <profile> config get terminal.cwd` to verify.

## 6. Enable toolsets (CLI and cron surfaces)

Tools are bound to **execution surfaces**, not profiles. A cron-run scout
agent runs on the `cron` platform; an interactive scout runs on `cli`.
Enable the configured toolsets on both platforms for any profile that needs
them.

The scaffold reads `hermes.profiles.<name>.toolsets` from `triage.yaml` and
emits:

```bash
hermes -p <profile> tools enable <tool> [<tool> ...] --platform cli
# For cron-owning profiles, repeat on the cron surface:
hermes -p <profile> tools enable <tool> [<tool> ...] --platform cron
```

`preflight` first checks that every configured name appears in the installed
runtime's availability list, then checks whether each available name is enabled
on every required surface. This catches unknown names even when `tools enable`
silently no-ops. Always enable tools through `hermes tools enable` and
write profile config through `hermes config set` — never reach into
`~/.hermes/profiles/<profile>/config.yaml` by hand. Hand-written config drifts
the next time the profile gets recreated.

> The supported Hermes 0.20 contract names the cron platform `cron`. The
> planner emits that reviewed command, but the opening checkpoint requires
> `capability.cron-tool-surface` and `capability.toolset-names.cron` to pass
> before you execute it. An older or forked runtime with a different surface
> is unsupported until its configuration/planner contract is updated explicitly.

## 7. Set dispatcher topology

The configured gateway profile is the **sole** Kanban dispatcher. In this
deployment that is the existing `default` profile and its running Discord
gateway. Every cron-owning
scout profile has its own gateway only to tick its profile-local cron; it must
not dispatch.

The scaffold emits:

```bash
hermes -p default        config set kanban.dispatch_in_gateway true
hermes -p <scout> config set kanban.dispatch_in_gateway false
```

`preflight` checks the resulting `gateway list` and `cron status` outputs.

## 8. Install the skills (manual, two supported paths)

The scaffold does not write live profile directories. Choose ONE of:

- **Reviewed local copy.** `python -m cli.triage render-skills` writes
  `work/scaffold/profiles/<profile>/skills/<skill>/SKILL.md` and prints the
  matching live destination for each rendered file. The default profile's
  orchestrator skill goes to `$HERMES_HOME/skills/triage-orchestrator/SKILL.md`.
  Review the local file, then copy it to the printed live
  destination yourself.
- **Profile distribution.** Package the rendered profile tree as a Hermes
  profile distribution and install it once `hermes profile install <local>`
  is supported by your target runtime. This is the path of least
  per-profile toil but is not implemented in this template yet.

Either way, automatic `hermes profile install` is intentionally deferred. Do
not script it.

## 9. Model + provider auth (manual checkpoints)

The scaffold emits one `CHECKPOINT:` per profile for model/provider selection
and provider authentication. Per-profile auth is **not** shared: authentication
on `default` does not cover `xresearch`. Use each profile's
interactive login flow (or `hermes profile show` to inspect the resolved
config) and never commit credentials. The planner does not invent provider
commands; it surfaces the requirement and stops.

> Web-search keys and similar `.env` values belong in each profile's `.env`.
> They are part of the auth checkpoint, not the toolset. The repo's
> `.gitignore` excludes `.env`.

## 10. Configure the gate channel

The gate uses the existing root/default Discord gateway. Keep its credentials in
`$HERMES_HOME/.env` (not in this repository): `DISCORD_BOT_TOKEN`,
`DISCORD_ALLOWED_USERS`, and `DISCORD_HOME_CHANNEL`. The configured target is
`discord:1484142557704491119` (`Gaymerz / #briefs`). Verify target discovery:

```bash
hermes -p default gateway status
hermes -p default send --list discord
```

Do not install or run a second gateway with the same Discord bot token. The
existing default gateway already owns inbound Discord. Sending a live delivery
check is an external side effect; do it only when explicitly approved:
`hermes -p default send --to discord:1484142557704491119 "delivery check"`.

> ⚠️ **Approval boundary.** Every Discord reply verb is a model-driven
> interpretation of the human's text. The orchestrator skill must invoke
> `proposal_actions.py {approve|shelve|shelve-all|modify}` deterministically.
> Status fields do not notify anyone. `python -m cli.triage install` does
> **not** send anything.

## 11. Register the scout crons (profile-local)

Hermes 0.20 cron is profile-local. Each cron-owning scout profile registers
its own job and runs its own gateway so the scheduler ticks. The scaffold
emits one `hermes -p <scout> cron create '<schedule>' '<prompt>' --name
<job-name> --skill <skill> --workdir <abs> --deliver local` per source. The
prompt is the source's `query` from `triage.yaml`.

> **`--deliver local`** is intentional. Scout jobs create intake cards on
> the board; their purpose is not to send you a cron response. `--deliver
> local` keeps cron output out of Discord. The default gateway is what actually
> sends proposals when they become ready.

`--workdir` is required on every scout cron so AGENTS.md / CLAUDE.md /
.cursorrules inject and the worker CWD is the repo. Use the absolute path
to this repository.

After creation:

```bash
hermes -p <scout> cron list --all        # both jobs present under the right profile
hermes -p <scout> cron status            # scheduler running
```

`preflight --format json` confirms both for you.

## 12. Start the gateways

```bash
hermes -p default       gateway status  # reuse; do not install a duplicate
hermes -p <scout>       gateway install --start-now --start-on-login  # once per cron-owning scout profile
```

Each cron-owning scout needs its own running gateway so its scheduler ticks
and `hermes cron runs <job>` resolves a profile-local execution attempt.
On WSL use `hermes -p <profile> gateway run --foreground` instead of
`gateway install` if you lack systemd.

Verify:

```bash
hermes gateway list                      # which profiles are active
hermes -p <profile> gateway status       # per-profile service state
hermes -p <scout>   cron status          # scheduler running
```

## 13. Smoke-test one cycle

Run a scout manually before relying on the cron tick:

```bash
hermes -p <scout> chat --skills <scout-skill> -q "Run one sweep now, following the skill exactly."
hermes kanban --board <board> list       # watch cards appear + promote
```

Expected flow: `intake → (dedup/score) → research lanes (parallel) → route →
prep → propose` → **proposal DM** → you reply `approve <slug>` →
`fulfill chain` → deliverable DM. Confirm the first post-gate card is `ready`
(not `todo`).

## 14. Go live

```bash
hermes -p <scout> cron resume <job-id>    # start with one scout
# watch a real cycle, then resume the others
```

Keep all gateways running. Cron tick and Kanban dispatch both depend on
their respective gateways being up.

## Day-to-day

- **Watch:** `hermes kanban --board <board> list`. Progress also appears in
  Discord `#briefs` through the default gateway.
- **Decide:** reply (no slash) `approve <slug>` / `shelve <slug>: reason` /
  `modify <slug>: change`; `reject the rest` (or `python proposal_actions.py
  shelve-all`) clears the queue.
- **Cost:** `python scripts/cost_report.py <slug> --gate <usd>`.
- **Inspect cron attempts:**
  `hermes -p <scout> cron runs <job-id>` (or `hermes cron runs --limit 50`
  across profiles).
- **Stop:** `Ctrl-C` the foreground gateway, or
  `hermes -p <profile> gateway stop`.

## Approval boundaries (publish / credentials)

- **Never commit** `.env`, `auth.json`, board `*.db`, `work/`, `vault/`,
  or anything under per-profile Hermes dirs. The repo's `.gitignore` covers
  these; verify before any push.
- **`install` is a stub.** It does not send messages, does not start
  gateways, and does not register crons. Nothing here ships a model,
  provider key, or Discord token.
- **No model/auth command is invented.** Every profile's provider and
  gate-channel auth is a manual checkpoint surfaced by the scaffold.
- **Cron owns nothing you didn't write.** `--deliver local` keeps scout
  cron output silent; outbound delivery is the orchestrator's job and goes
  through the human-gate path.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `scaffold` emits a preflight blocker summary to stderr | Run `python -m cli.triage preflight --format json` for evidence. The plan still renders; review it before executing. |
| Scout runs, no card appears | Scout profile missing a toolset on the `cron` platform. Run `hermes -p <scout> tools list --platform cron` and re-enable missing toolsets (`hermes tools enable … --platform cron`). See step 6. |
| Crons never fire | The owning profile's gateway is not running. Run `hermes -p <scout> gateway status` and `hermes gateway list` to confirm. The job is registered under one profile; the scheduler must tick under that same profile. |
| Card stuck in `todo` | It has an unfinished parent. Don't parent the first post-gate task to the triage card. |
| Proposal status set but no DM | Orchestrator didn't `hermes send`; status ≠ delivery (docs/05). |
| `/approve` triggers the wrong approval flow | Hermes reserves it for command execution; reply with ordinary text `approve <slug>`. |
| Final delivery can't find artifacts | A stage used scratch, not the persistent `dir` workspace. |
| `gateway start` fails on WSL | Use `hermes -p <profile> gateway run` (foreground). |
| `gateway install` is invasive on disposable rehearsal | Use `hermes -p <profile> gateway run --foreground` or run the rehearsal with `HERMES_RUN_DISPOSABLE_REHEARSAL=1 python -m unittest tests.integration.test_scaffold_disposable_home -v` (it sanitizes `HOME`/`HERMES_HOME`/`TMPDIR`, refuses mutation unless Hermes discovers an isolation sentinel, and cleans up automatically). |
| Toolset name rejected by installed Hermes | Track upstream Hermes issue #64494 first; the template intentionally retains `kanban`/`coding` and preflight reports names absent from the installed availability set. If you deliberately choose a local compatibility override, change `hermes.profiles.<name>.toolsets` in `triage.yaml` to runtime-advertised names, document the divergence, and rerun validate/preflight. No silent rename is performed. |
| `hermes cron runs <job>` returns empty | Job id wrong or job is paused. `hermes -p <scout> cron list --all` to find the right id. |
| `hermes gateway list` empty | Gateways not installed yet (`hermes -p <profile> gateway install --start-now --start-on-login`), or you're in a temporary home without sentinel files. |

> Every command above was checked against the installed Hermes 0.20.0 help
> surface (`hermes <subcommand> --help`) and against the live preflight
> capability checks. If your Hermes version drifts, the
> `tests/test_hermes_cli_contract.py` opt-in test will catch a flag the
> scaffold no longer supports — fix the planner or bump
> `hermes.min_version`, do not patch the docs to invent a flag.
