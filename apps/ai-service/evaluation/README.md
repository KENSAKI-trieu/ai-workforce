# Evaluation

Versioned evaluation assets must contain only synthetic or explicitly approved,
non-sensitive data. Shared RAG metrics live in `app/rag/evaluation`.

## Pre-LangChain baseline

The phase-1 baseline covers Legal, HR, Finance, Knowledge and general chat with:

- deterministic agent-routing accuracy;
- grounded term recall and citation coverage;
- local-provider token usage and contract accuracy;
- average and p95 in-process latency;
- estimated provider cost (zero for the offline local-provider run).

Run it from `apps/ai-service`:

```powershell
python -m evaluation.run_baseline \
  --output evaluation/baselines/pre_langchain_v1.json
```

The report intentionally records current gaps. In particular, the pre-migration
AI Service registry does not contain Legal and Knowledge agents. The baseline does
not call external models and therefore must not be interpreted as a production LLM
quality or end-to-end latency benchmark.
