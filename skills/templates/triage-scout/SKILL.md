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
unset and kanban tools are NOT auto-enabled. The scout profile MUST list `kanban`
in its `toolsets:` or `kanban_create` silently does nothing. (The scaffolder sets
this; verify it.)

## What to look for

{{QUERY}}

## Procedure

1. Search your assigned surface for candidate items matching the query above.
2. For each distinct candidate, capture: a one-line **claim**, **source URLs**
   (every claim traceable to a primary source), a verbatim quote where possible,
   and one line on **why it may matter**.
3. Drop low-signal noise — vague hype, single-person rants with no corroboration.
4. Write the full report to:
   `{{INTAKE_DIR}}/<UTC-timestamp>-{{SOURCE_ID}}.md`
   in the format below.
5. Create ONE intake Kanban task on the triage board:

   ```
   kanban_create(
     board: "{{BOARD}}",
     title: "intake: {{SOURCE_ID}} <UTC-date>",
     assignee: "orchestrator",
     body: "<path to the report file you just wrote>",
   )   # no parents → lands `ready`; the orchestrator picks it up
   ```

## Report format (contract with engine/intake_parser.py)

```
source: {{SOURCE_ID}}
captured_at: <UTC timestamp>

## Candidate: <title>
Claim: <one-line claim>
Sources:
  - url: https://...
    quote: "verbatim"
Why it may matter: <one line>

## Candidate: <title>
...
```

If you change these fields, update `item_schema` in `triage.yaml` AND
`engine/intake_parser.py` to match.

## Don't

- Don't dedup, score, or route — that's the orchestrator's job. You only detect.
- Don't post anywhere except the intake vault + the one intake task.
- Don't fabricate sources. No URL → don't include the claim.
