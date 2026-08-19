# Research lane guide

Follow only the section matching the lane named in your task. Read the item and
`domain/ai-engineering-skills-map.md`. Write a source-backed handoff with retrieval
dates, explicit uncertainty, and no fabricated evidence.

## `verify_sources`

Verify provenance, dates, quotations, and material claims. Separate primary
evidence, practitioner reports, and vendor claims. Return a claim-to-source table,
conflicts, limitations, and an evidence-strength judgment.

## `map_skill_dependencies`

Choose exactly one primary Skills Map topic and named subskills. Define observable
current and target capabilities. Produce the smallest prerequisite DAG connecting
them. For every node provide `id`, observable capability, `depends_on`, disposition
(`assumed_known`, `diagnostic`, or `instructional`), and evidence for non-obvious
dependencies.

## `model_target_learner`

Describe the software engineer behaviorally: what they can build, explain,
measure, and debug now. Identify likely misconceptions and observable diagnostic
signals. Avoid labels such as beginner or intermediate without behavioral detail.

## `audit_existing_learning`

Audit current docs, courses, talks, labs, and reference implementations against
the dependency DAG. Report coverage, freshness, prerequisites, practice,
feedback, unexplained jumps, inconsistent terminology, and remaining gap.

## `design_assessment`

Design a short initial diagnostic, checks at important dependency boundaries,
and an authentic terminal task. Prefer prediction, explanation, implementation,
debugging, or measurement over confidence questions. Include misconception-based
distractors and remediation signals.

## `recommend_learning_format`

Synthesize the available lane evidence. Choose the smallest sufficient format:

- `focused_knowledge_gap`: short dependency chain; engineering judgment is the goal.
- `conceptual_model_gap`: one missing mental model blocks otherwise capable engineers.
- `applied_skill_gap`: implementation, measurement, debugging, or tool use is essential.
- `broad_dependency_gap`: several capabilities must be developed in sequence.
- `learner_variability_high`: learners enter from materially different DAG nodes.
- `already_covered`: strong current material already closes the gap.
- `insufficient_evidence`: the claimed need is not adequately supported.

End with exactly this YAML handoff; `recommended_format` must be one value above:

```yaml
recommended_format: applied_skill_gap
primary_skill: building_and_deploying_ai
primary_subskills: [evals, error_analysis]
learner_profile: application_software_engineer
current_capability: can_build_basic_rag
target_capability: can_diagnose_rag_failures
dependency_breadth: medium
practice_requirement: high
learner_variability: medium
evidence_strength: high
existing_coverage: fragmented
confidence: 0.84
```
