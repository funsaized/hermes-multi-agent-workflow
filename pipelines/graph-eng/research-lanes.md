# Graph engineering research lane guide

## Shared definitions

A **context graph** is a versioned, provenance-bearing model of delivery context:
requirements, decisions, architecture, code, tests, ownership, policy, runtime
signals, and the relationships between them. Keep it distinct from:

- a workflow graph, which controls execution order;
- a code/dependency graph, which may be one subgraph;
- a knowledge graph or GraphRAG implementation, which is one possible mechanism;
- a chat transcript or vector index with no explicit relationship model.

Every lane must name the graph kind, distinguish demonstrated facts from proposed
design, cite primary sources, and record contrary evidence or uncertainty.

## Lane contracts

### `verify_evidence`

Verify canonical sources, dates, authorship, quotes, linked artifacts, and product
availability. For X evidence, confirm the canonical post and thread; do not infer
technical facts from an unverified post. Output a claim-to-source table.

### `classify_graph_pattern`

Identify the graph kind, delivery stage, users, system boundary, and whether the
pattern is implemented, measured, proposed, or merely named. Flag overloaded
uses of “graph” and separate context modeling from orchestration.

### `model_context_graph`

Propose the smallest useful node/edge schema, identifiers, provenance, temporal
semantics, ingestion/update rules, retrieval queries, and stale-data handling.
Show how requirements and decisions trace to code and tests.

### `map_copilot_workflow`

Map the graph to an explicit GitHub Copilot surface: Chat/agent mode, cloud coding
agent, repository instructions, prompt files, custom agents, skills, hooks,
Spaces, code review, or approved MCP. Specify task input, context supplied,
permissions, expected change, validation, and human review. Verify current GitHub
support instead of assuming feature parity between IDE, CLI, and cloud agents.

### `audit_enterprise_constraints`

Cover data classification, tenant/repository boundaries, least privilege,
secrets, prompt injection, source provenance, retention, licensing, audit logs,
policy controls, failure recovery, and required approvals. State what must remain
read-only and what may write only after the human gate.

### `design_tutorial_and_evaluation`

Define the learner’s current and target capabilities, a synthetic example, the
smallest practice loop, automated checks, a baseline without the graph, and
measures for correctness, context quality, review effort, and delivery time.

### `recommend_artifact`

Synthesize all lanes and emit exactly one `recommended_format`:

- `focused_pattern_gap`: explanation plus a compact applied walkthrough;
- `implementation_pattern_gap`: a complete, runnable worked example;
- `guided_practice_gap`: a starter/solution lab with feedback;
- `system_design_gap`: a multi-component enterprise reference package;
- `already_covered`: a current, credible resource already closes the gap;
- `insufficient_evidence`: claims cannot support a safe, useful artifact.

Explain why the chosen format is the smallest one that closes the verified gap.
