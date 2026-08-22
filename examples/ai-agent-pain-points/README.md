# Example: ai-agent-pain-points (historical reference)

This folder preserves historical reference material for the design the template
was extracted from. It is not a second config or a current deployment contract.
The active configs are repo-root `triage-ai-engineering.yaml` and
`triage-graph-eng.yaml`; they may evolve away from this snapshot. For the
origin-system write-up, see **`REFERENCE.md`**.

## What it does

- **Scouts** (`xresearch` on Grok, `webresearch` on GPT) watch X, Reddit,
  YouTube, and the web for concrete pain points AI-agent users hit.
- **Rubric** scores frequency / intensity / solvable-or-explainable / solution
  gap / strategic fit; threshold 65/100.
- **Research** verifies sources, gathers prior context, and audits existing
  solutions — the audit emits `solution_quality`.
- **Route:** `missing`/`broken` → **build** a fix; `confusing`/`poorly_documented`/
  `outdated` → make an explainer **video**; `good` → **shelve**.
- **Gate:** one Discord approval per item in `Gaymerz / #briefs`.
- **Fulfill:** build path → prototype → test → report; video path → slides →
  script → deliver.

## Reference skill (historical snapshot)

`reference-skills/pain-point-scout-x/SKILL.md` is an old concrete X/Grok scout
retained for comparison. Its board, profile, and vault paths do not come from the
current config, so do not install it as-is. Current source-specific skills are
generated from `skills/templates/triage-scout/SKILL.md` by `render-skills`.

## Use it as a starting point

Edit a root pipeline config (e.g. `triage-ai-engineering.yaml`), then follow
`docs/04-adapting-to-your-domain.md` to repoint it. The structure (sources → rubric → research → route → paths → gate)
stays the same; you swap the content.

## The pattern generalizes

The same skeleton fits, for example:

- **GitHub issue triage** — sources: repo issues + discussions; rubric:
  severity/frequency/reproducibility; route: `bug`→fix path, `docs-gap`→docs path,
  `wontfix`→shelve.
- **Sales-lead triage** — sources: inbound forms + mentions; rubric: fit/intent/
  budget; route: `qualified`→outreach path, `nurture`→sequence path, `junk`→shelve.
- **Support-ticket triage** — sources: ticket queue; rubric: severity/SLA/scope;
  route: `known`→auto-reply path, `bug`→escalate path, `unclear`→clarify path.

In every case you edit `triage.yaml` and the `paths/` templates — never the engine.
