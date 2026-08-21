# Deliverable spec: enterprise reference package

Produce a bounded reference architecture plus local demonstrator, with a
`README.md` entry document (the primary file the delivery hook sends),
containing:

- context, goals, non-goals, actors, trust boundaries, and threat model;
- a typed graph schema spanning requirements, decisions, code, tests, ownership,
  policy, and runtime evidence, with provenance and temporal semantics;
- ingestion, reconciliation, invalidation, retrieval, and access-control design;
- an explicit mapping to supported GitHub Copilot surfaces and enterprise policy
  controls, including least-privileged MCP/API boundaries;
- observability, audit, evaluation, rollout, and failure-recovery plans;
- a synthetic local demonstrator and deterministic verification command; and
- ADRs for material choices and a build-versus-buy boundary.

Do not imply that the demonstrator is production-ready. Clearly list scale,
security, availability, governance, and integration work required before adoption.
