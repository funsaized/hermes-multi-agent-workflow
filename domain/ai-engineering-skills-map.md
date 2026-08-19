# AI Engineering Skills Map taxonomy

This reviewed vocabulary anchors scouts, researchers, proposals, and learning
artifacts. Select one primary topic per item; use cross-topic dependencies when
the learning transition genuinely requires them. Continuous learning applies to
every topic rather than forming a fifth route.

## `building_and_deploying_ai`

- `llm_fundamentals`
- `context_engineering`
- `retrieval_augmented_generation`
- `agentic_workflows`
- `machine_learning_fundamentals`
- `deep_learning_fundamentals`
- `evals`
- `error_analysis`
- `statistical_measurement`
- `system_steering`
- `governance`

## `software_engineering_fundamentals`

- `requirements_and_specs`
- `architecture`
- `data_modeling`
- `testing`
- `observability`
- `reliability`
- `scalability`
- `performance`
- `security`
- `cost_tradeoffs`
- `maintenance`

## `using_coding_agents`

- `agent_mental_models`
- `context_management`
- `specification_strategy`
- `task_decomposition`
- `tool_and_permission_design`
- `multi_agent_orchestration`
- `verification`
- `production_safety`
- `workflow_evaluation`
- `continuous_tool_learning`

## `shaping_the_build`

- `problem_discovery`
- `customer_understanding`
- `product_judgment`
- `business_context`
- `outcome_definition`
- `scope_and_prioritization`
- `experimentation`
- `adoption`
- `impact_measurement`

## Common cross-topic relationships

These are frequent relationships, not mandatory universal prerequisites:

```text
requirements_and_specs -> specification_strategy -> agentic_workflows
evals -> verification -> impact_measurement
customer_understanding -> outcome_definition -> architecture -> cost_tradeoffs
error_analysis -> observability -> workflow_evaluation
security -> tool_and_permission_design -> production_safety
```

## Capability-writing rules

- Describe current and target states as observable behavior.
- Build the smallest prerequisite graph that connects those states.
- Mark nodes as `assumed_known`, `diagnostic`, or `instructional`.
- Do not add adjacent topics merely to make an artifact look comprehensive.
- Record evidence for non-obvious dependency claims.
