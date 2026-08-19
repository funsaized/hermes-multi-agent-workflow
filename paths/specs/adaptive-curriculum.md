# Deliverable spec — adaptive curriculum

Produce `capability-graph.yaml`, `diagnostic-bank.yaml`, `learner-state.schema.json`,
`lessons/`, `assessments/`, `navigate.py`, `test_navigate.py`, `README.md`, and
`source-ledger.md`.

The local navigator reads diagnostic results, marks demonstrated capabilities,
selects the nearest unmet dependency, records assessment outcomes, and chooses the
next node. Use the Python standard library and local files. Tests must simulate at
least three distinct learner entry states, including remediation after failure.
