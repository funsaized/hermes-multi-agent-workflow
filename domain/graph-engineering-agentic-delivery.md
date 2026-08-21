# Graph engineering for agentic software delivery

This pipeline uses **graph engineering** to mean deliberately modeling,
maintaining, retrieving, and evaluating relationships that help software agents
deliver changes with traceable context. The primary object is a context graph;
workflow, code, dependency, knowledge, and provenance graphs are related but not
interchangeable.

## Core capability map

1. **Model** — choose stable identities, typed nodes/edges, provenance, temporal
   validity, ownership, policy labels, and repository boundaries.
2. **Acquire and maintain** — ingest authoritative sources; reconcile conflicts;
   update incrementally; detect deletion, drift, and stale context.
3. **Retrieve and ground** — answer task-shaped graph queries; bound traversals;
   rank evidence; package compact, cited context for Copilot.
4. **Act and verify** — translate intent into a bounded issue/session; let an
   approved Copilot surface propose changes; run tests, policy checks, review,
   and trace results back to requirements and decisions.
5. **Govern and evaluate** — enforce least privilege and data boundaries; resist
   poisoned context; audit agent/tool actions; compare against a no-graph
   baseline for correctness, review effort, latency, and context freshness.

## Delivery relationships

The minimum useful graph connects:

`requirement → decision → component → code change → test → review evidence`

Enterprise extensions may add owners, policies, incidents, deployments, runtime
signals, and exceptions. Every extension must answer a concrete delivery query;
unused ontology is not progress.

## Copilot integration surfaces

Research may map graph context to repository instructions, path-specific
instructions, prompt files, custom agents, skills, hooks, Spaces, MCP servers,
Copilot Chat/agent mode, cloud coding-agent issues and pull requests, or code
review. Verify support and policy constraints per surface; do not assume that an
IDE, CLI, app, and cloud agent expose identical tools or context.
