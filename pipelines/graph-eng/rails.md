# Scope rails: graph engineering artifacts

- Use only public sources and synthetic, non-sensitive example data.
- Keep graph kinds explicit; do not relabel ordinary RAG or a task list as a
  context graph.
- Ground GitHub Copilot behavior in current official documentation and label
  preview, plan, client, and enterprise-policy dependencies.
- Examples must run locally without requiring access to a real enterprise,
  production repository, or paid graph database. Prefer files and standard
  formats unless a dependency teaches an essential concept.
- Never include secrets, tokens, private repository content, customer data, or
  copied proprietary schemas. Do not weaken repository or enterprise controls.
- MCP and API examples are read-only by default and least-privileged. Any write
  workflow must use a synthetic target, be clearly marked, and retain human PR
  review. X research never posts or mutates an account.
- Treat model-authored code and retrieved context as untrusted. Include input
  validation, provenance, prompt-injection boundaries, deterministic checks,
  and recovery guidance.
- Do not claim productivity, quality, or safety gains without a stated baseline
  and evidence. Label hypotheses as hypotheses.
