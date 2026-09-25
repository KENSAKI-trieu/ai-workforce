# AI cost metering

Cost reports include only usage confirmed by an LLM provider (or an explicit
manual import). Deterministic internal tools and the legacy hard-coded token
estimates are not billable usage.

## Recording provider usage

After a successful provider response, map its usage counters into
`log_llm_cost`:

```python
from app.services.audit_service import log_llm_cost

log_llm_cost(
    db=db,
    tenant_id=user.tenant_id,
    user_id=user.id,
    department=user.department,
    agent_role="HR",
    model_name=provider_response.model,
    prompt_tokens=provider_response.usage.input_tokens,
    cached_prompt_tokens=provider_response.usage.cached_input_tokens or 0,
    completion_tokens=provider_response.usage.output_tokens,
    usage_source="PROVIDER",
)
```

Do not estimate token usage from string length. Unknown model IDs intentionally
raise an error until a reviewed price is added to
`app/domains/platform/cost_calculator.py`.

## Reporting period and legacy data

- Cost endpoints default to the current UTC calendar month.
- Pass `?month=YYYY-MM` to query a different month.
- Rows produced by the old hard-coded estimator are retained with
  `usage_source=LEGACY_ESTIMATE` and excluded from totals.
- The summary exposes `legacy_records_excluded` so the UI can disclose this.

## Pricing changes

Prices are versioned by `PRICING_VERSION` and persisted with each usage row.
When provider pricing changes, add a new reviewed pricing version rather than
silently recalculating historical rows.
