# LangChain Tool Registry - Phase 3

## Architecture

The AI service owns LangChain `StructuredTool` wrappers and Pydantic schemas. It
does not import database models or execute business operations. Every tool call
is sent to the backend endpoint:

```text
POST /api/v1/internal/tools/{tool_name}/invoke
Authorization: Bearer <short-lived internal_tool JWT>
```

The backend registry is authoritative for ACL checks and execution. It validates
the JWT audience, issuer, type, actor, tenant, active account, role, department,
AI Employee configuration, and Pydantic input before calling an executor. Role,
department, actor, and tenant claims supplied inside LLM-generated data are never
used for authorization.

## Registered Tools

| Tool | Classification | Timeout | Attempts |
| --- | --- | ---: | ---: |
| `rag_search` | `READ_ONLY` | 20s | 3 |
| `employee_lookup` | `READ_ONLY` | 10s | 3 |
| `leave_lookup` | `READ_ONLY` | 10s | 3 |
| `create_task` | `WRITE` | 10s | 1 |
| `expense_lookup` | `READ_ONLY` | 20s | 3 |
| `generate_legal_document` | `WRITE` | 45s | 1 |
| `submit_approval_request` | `EXTERNAL_ACTION` | 10s | 1 |

All inputs require `tenant_id` and `audit.correlation_id`. Mutating tools also
require `audit.idempotency_key`. PostgreSQL transaction advisory locks and the
successful audit result make repeated mutating requests idempotent.

## Internal JWT

Use `create_internal_tool_token(user, agent_role=...)` on the backend. Tokens are
valid for five minutes by default and are bound to the actor and tenant. When an
`agent_role` is present, the gateway also verifies that tenant's active AI
Employee configuration (`tools_access`, `allowed_actions`, and
`disallowed_actions`).

The AI service creates request-scoped tools with:

```python
gateway = ToolGatewayClient(settings.BACKEND_TOOL_GATEWAY_URL, internal_jwt)
tools = build_langchain_tools(gateway)
```

The internal JWT is client context, not part of a LangChain tool schema, so the
model cannot replace it.

## Audit

Each invocation writes a tenant-scoped `AuditLog` with the authoritative actor,
agent role, action classification, correlation data, status, and duration.
Raw input values are not copied into the gateway audit record; only field names
and trace metadata are retained. Existing domain services may add their own
more specific audit events.
