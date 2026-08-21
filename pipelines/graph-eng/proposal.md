# Proposal: {{ item.title }}

**Route:** {{ route }}  
**Capability transition:** {{ current_capability }} → {{ target_capability }}  
**Graph kind / Copilot surface:** {{ graph_kind }} / {{ copilot_surface }}

## Evidence and decision

Summarize the verified claim, strongest primary sources, score, competing route,
and why this is the smallest artifact that closes the gap. State uncertainty,
enterprise risks, and anything the evidence does not establish.

## Deliverable

List the files, runnable checks, learner outcome, and acceptance criteria. Include
the synthetic scenario and the baseline against which the graph-assisted
workflow will be evaluated.

## Gate

Reply with ordinary text using the pipeline-qualified reference:

- `approve graph-eng:{{ item.slug }}`
- `modify graph-eng:{{ item.slug }} <requested change>`
- `shelve graph-eng:{{ item.slug }}`
