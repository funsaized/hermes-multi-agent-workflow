# Contributing

Thanks for your interest. This project is a **template** — a skeleton people fork
and adapt — so contributions that keep it generic and well-documented are the most
valuable.

## Ground rules

1. **Keep the engine domain-agnostic.** `engine/` must not contain any subject
   matter. Domain logic belongs in the pipeline configs (`triage-*.yaml`) and
   the `paths/` / `skills/` templates. PRs that hardcode a domain into the engine will be asked to move it
   to config. (See `AGENTS.md`.)
2. **Add mechanisms, not topics.** Good engine PRs add new *capabilities* (an
   embedding dedup backend, a new scoring mode, a new step type) — not new
   domains.
3. **Keep tests green.** `python -m unittest discover -s tests` must pass. Add
   tests for new mechanisms; cover them against a synthetic config like the
   existing ones.
4. **Validate config changes.** `python -m cli.triage --config <pipeline>.yaml
   validate` after any change to a pipeline config or the config schema.
5. **Never commit secrets or real data.** See `SECURITY.md` and `docs/06`.

## Dev setup

```bash
pip install -r requirements.txt
python -m unittest discover -s tests     # full suite
python -m cli.triage --config triage-ai-engineering.yaml validate
```

No build step; it's plain Python (3.10+) plus PyYAML.

## What's especially welcome

- New `examples/<domain>/` configs that show the engine fitting a different
  problem (GitHub issue triage, lead triage, support-ticket routing, …).
- Docs improvements — clearer adaptation guidance, more gotchas.
- An embedding-based dedup backend, including wiring `dedup.method` through
  `TriageEngine` and defining storage/loading for existing embeddings.
- A separately approved installer that applies the existing dry-run scaffold
  plan end to end.

## Style

Match the surrounding code: typed dataclasses, clear docstrings aimed at the
*adapting agent*, comments that explain *why*. Prefer stdlib; justify new
dependencies.

## Hermes CLI compatibility updates

The deployment planner targets the minimum Hermes version declared at
`hermes.min_version` in the pipeline config. When adopting a new Hermes release:

1. Review the release's command help, then update generated argv and the minimum
   version together. Do not change a command merely to match cosmetic help text.
2. Run `validate` and both scaffold formats against each shipped config. The planner's
   pure unit tests remain the authority for exact ordering and argv.
3. Run `HERMES_RUN_CLI_CONTRACT=1 python -m unittest tests.test_hermes_cli_contract -v` with Hermes
   installed. The live test checks every generated subcommand and long flag;
   without Hermes it skips with an explicit reason.
4. Render and review profile skills with `python -m cli.triage render-skills`.
   Installation remains manual. Either copy each printed local `SKILL.md` to
   its exact printed destination (`$HERMES_HOME/skills/...` for the base profile,
   `$HERMES_HOME/profiles/<profile>/skills/...` for clones), or package the rendered profile tree as a Hermes profile
   distribution. Automatic `hermes profile install` is intentionally deferred.
5. Rehearse isolated state with
   `HERMES_RUN_DISPOSABLE_REHEARSAL=1 python -m unittest tests.integration.test_scaffold_disposable_home -v`.
   This uses temporary `HOME` and `HERMES_HOME`, refuses mutation unless Hermes
   discovers an isolation sentinel, never starts gateways, and cleans up.
6. Run the full suite and inspect the shell/JSON plan before changing any live
   profile. `python -m cli.triage install` must remain a stub until profile
   distributions and a separately approved installer are implemented.
