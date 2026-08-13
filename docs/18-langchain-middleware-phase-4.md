# LangChain Middleware and Guardrails - Phase 4

Reference: [LangChain middleware overview](https://docs.langchain.com/oss/python/langchain/middleware/overview)
and [custom middleware hooks](https://docs.langchain.com/oss/python/langchain/middleware/custom).

## Runtime Contract

The backend creates `AgentRuntimeContext`; model output never creates or changes
it. The context contains actor/tenant identity, role, department, AI Employee
role, allowed and denied tools, correlation identifiers, citation scope, and an
optional complexity hint.

The AI middleware filters tools and overwrites every tool call's `tenant_id` and
`audit` arguments from this context. This is an early guard only. The backend
tool gateway remains authoritative and revalidates the internal JWT, database
actor, tenant, ACL, and AI Employee configuration.

## Ordered Stack

1. `TenantACLContextMiddleware`: inject trusted context, filter model-visible
   tools, deny unlisted calls, and overwrite tenant/audit arguments.
2. LangChain `PIIMiddleware`: redact email, credit card, IP address, and Vietnam
   phone formats across input, output, tool results, and streaming surfaces.
3. `ModelCallLimitMiddleware` and `ToolCallLimitMiddleware`: cap one run at 8
   model calls and 12 tool calls by default.
4. `ComplexityModelSelectionMiddleware`: select simple or complex model using a
   trusted hint or deterministic message/tool complexity score.
5. `ModelFallbackMiddleware`: move to configured secondary providers/models.
6. `ModelRetryMiddleware`: retry transient model/output-validation failures with
   bounded exponential backoff.
7. `OutputCitationValidationMiddleware`: reject empty final output, missing
   citation markers, or text/structured citations outside the supplied sources.
8. `ModelTelemetryMiddleware`: record every physical model attempt so retries
   and fallbacks are included in token, cost, and latency accounting.

Output validation wraps telemetry. Therefore a provider response rejected for a
bad citation is still metered before retry. Model selection wraps fallback, so a
fallback override is not replaced by the complexity selector.

## Usage and Cost

The request-scoped `GatewayTelemetrySink` posts provider usage to:

```text
POST /api/v1/internal/tools/model-usage
Authorization: Bearer <internal_tool JWT>
```

The backend binds usage to the JWT actor and tenant, rejects an agent-role
mismatch, calculates USD cost from its versioned pricing table, writes
`LLMCostLog`, and writes latency/correlation metadata to `AuditLog`. Failed
provider calls have no reliable token count, so they write latency and failure
audit data without fabricating cost.

## Configuration

```text
AGENT_MAX_MODEL_CALLS=8
AGENT_MAX_TOOL_CALLS=12
AGENT_MIDDLEWARE_MODEL_RETRIES=2
AGENT_COMPLEXITY_THRESHOLD=4
```

Provider SDK retries should be set to zero for agents using middleware retries
to avoid multiplying attempts across two retry layers.
