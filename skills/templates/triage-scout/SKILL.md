---
name: {{SKILL_NAME}}
description: >
  Source-specific scout for {{SOURCE_ID}}. Runs on a cron under {{PROFILE}}, searches
  for candidate items, submits a report to the configured intake vault, and creates one
  intake Kanban task on board {{BOARD}}.
metadata:
  hermes:
    tags: [triage, scout, intake]
---

# Triage scout: {{SOURCE_ID}}

This rendered skill belongs to profile `{{PROFILE}}` and runs from
`{{PROJECT_ROOT}}`.

## When to use

Fired hourly by cron (the `schedule` in `triage.yaml`). Also runnable by hand for
a smoke test:

```
hermes -p {{PROFILE}} chat --skills {{SKILL_NAME}} -q "Run one sweep now, following this skill exactly."
```

## Prerequisite (read once)

This skill runs via **cron, not the dispatcher**, so `HERMES_KANBAN_TASK` is
unset and model-level `kanban_*` tools are not injected. Use the enabled
`terminal` tool to call `scout_actions.py`; it scopes the report path and creates
the first card through the Hermes CLI. Workers later spawned from that card
receive dedicated Kanban model tools automatically and should use those instead.

## What to look for

{{QUERY}}

## Procedure

1. Search your assigned surface for candidate items matching the query above.
2. For each distinct candidate, capture: a one-line **claim**, **source URLs**
   (every claim traceable to a primary source), a verbatim quote where possible,
   and one parseable **why it may matter** line. Put query-required labels on that
   same line separated by semicolons; the current parser does not consume
   continuation lines.
3. Drop low-signal noise — vague hype, single-person rants with no corroboration.
4. Write the full report as a draft anywhere under the project workspace, using
   the format below. Submit it through the config-scoped helper:

   ```text
   python scout_actions.py --config "{{CONFIG_PATH}}" {{SOURCE_ID}} --report-file "<absolute draft path>"
   ```

   The helper validates the report, derives `{{INTAKE_DIR}}` from the selected
   config, writes only there, and creates the one intake card idempotently through
   the Hermes CLI. Parse its JSON result and require `ok: true` plus `task_id`.
   Never choose a vault path yourself or call `hermes kanban create` directly.

   Pipeline contract: `{{PIPELINE_ID}}`, config `{{CONFIG_PATH}}`. Never create
   this intake on another board, even if another pipeline uses the same source.

## Report format (contract with engine/intake_parser.py)

```
source: {{SOURCE_ID}}
captured_at: <UTC timestamp>

## Candidate: <title>
Claim: <one-line claim>
Sources:
  - url: https://...
    quote: "verbatim"
Why it may matter: <one line; labeled details required by the source query>

## Candidate: <title>
...
```

If you change these fields, update `item_schema` in `triage.yaml` AND
`engine/intake_parser.py` to match.

## Don't

- Don't dedup, score, or route — that's the orchestrator's job. You only detect.
- Don't post anywhere except the intake vault + the one intake task.
- Don't fabricate sources. No URL → don't include the claim.
