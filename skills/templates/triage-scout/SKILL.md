---
name: {{SKILL_NAME}}
description: >
  Source-specific scout for {{SOURCE_ID}}. Runs on a cron under {{PROFILE}}, searches
  for candidate items, writes a report to the shared intake vault, and creates one
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
unset and model-level `kanban_*` tools are not injected. Hermes assigns cron and
scripts to the CLI surface: use the enabled `terminal` tool to create the first
card with `hermes kanban`. Workers later spawned from that card receive the
dedicated Kanban model tools automatically and should use those instead of CLI.

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
4. Write the full report to:
   `{{INTAKE_DIR}}/<UTC-timestamp>-{{SOURCE_ID}}.md`
   in the format below.
5. Reuse the report's UTC timestamp in the title and idempotency key. Create ONE
   intake task through the CLI and parse the JSON result to confirm success:

   ```text
   hermes kanban --board "{{BOARD}}" create "intake: {{SOURCE_ID}} <UTC-timestamp>" --body "<absolute report path>" --assignee "{{ORCHESTRATOR_PROFILE}}" --workspace "dir:{{PROJECT_ROOT}}" --created-by "{{PROFILE}}" --skill triage-orchestrator --idempotency-key "intake:{{SOURCE_ID}}:<UTC-timestamp>" --json
   ```

   No parent means the card lands `ready`. `--workspace` pins the repository so
   the orchestrator can find the report, config, and engine. On a retry, use the
   same key; Hermes returns the existing non-archived task instead of duplicating
   it. If the command fails or its JSON does not contain a task id, report the
   failure and do not claim intake succeeded.

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
