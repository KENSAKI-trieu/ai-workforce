# Evaluation

Versioned evaluation assets must contain only synthetic or explicitly approved,
non-sensitive data. Shared RAG metrics live in `evaluation/metrics/`.

## Pre-LangChain baseline (archived)

`baselines/pre_langchain_v1.json` records the phase-1 baseline, measured against the
keyword agent registry and the local provider that the AI service used before the
LangChain migration. That registry and its `/v1/agents/route` endpoint have since been
removed -- the backend always names the agent a turn goes to -- so the runner that
produced the report was removed with them. The report and `datasets/baseline_v1.json`
are kept as a historical record and as seed cases for the live evaluation that gates
moving each agent onto LangGraph.
