# Enterprise Operations Expansion Plan

Date: 2026-07-31
Scope: Management Analytics, Audit Log, Company Settings, Notifications, Integrations, CI/CD

## 1. Product boundaries

- Keep the existing CEO Dashboard at `/dashboard`.
- Add a separate management analytics experience at `/analytics`.
- Enforce tenant isolation on every query and mutation.
- Never store provider API keys, OAuth refresh tokens, webhook secrets, or passwords in plaintext.
- Treat destructive company-data deletion as a reviewed request, not an immediate UI action.
- Make estimates explicit. “Hours saved” is an operational estimate, not an accounting fact.

## 2. Management Dashboard

### Questions answered

- How many tasks were completed, failed, overdue, or waiting for approval?
- Which tasks are late or at risk?
- Which agents have the best success rate and which fail most often?
- Which workflows fail repeatedly?
- How many tokens and how much estimated AI cost were consumed?
- How much human time may have been saved?
- How many agents are active and how satisfied are users?

### Metrics and methodology

- `tasks_completed`: tasks with status `COMPLETED` in the selected period.
- `success_rate`: completed / (completed + failed), excluding cancelled work.
- `average_execution_time`: average audit execution time for measurable actions.
- `human_approval_rate`: approved / (approved + rejected) decisions.
- `pending_approvals`: approvals currently in `WAITING`.
- `failed_workflows`: workflows ending in `FAILED`.
- `token_usage`: provider-reported prompt + completion tokens.
- `estimated_cost`: immutable metered cost snapshots.
- `hours_saved`: max(completed tasks × configurable baseline - measured AI execution, 0).
- `active_agents`: active agents in the visible tenant/department scope.
- `user_satisfaction`: positive feedback / all rated assistant messages.

### Cases

- Empty period: return zero-valued metrics and empty arrays, never fake trends.
- Running task without execution timing: omit it from execution average.
- Manager access: scope task and actor data to the manager’s department.
- Overdue calculation: derive from due date even if the persisted status has not yet been updated.
- Legacy cost rows: exclude non-provider/approved import sources consistently.
- Division by zero: return `0`, not NaN.

## 3. Audit Log

### Recorded fields

- Tenant, actor user, actor type (`USER`, `AGENT`, `SYSTEM`), agent role.
- Action, tool, resource type/id, workflow.
- Before and after snapshots.
- Sanitized input/output metadata.
- IP address, user-agent/device hint.
- Success/failure status, error message, execution time, timestamp.

### Security cases

- Sensitive keys (`password`, `token`, `secret`, `authorization`, `api_key`) are recursively redacted.
- Cross-tenant resource IDs return no data.
- Manager audit visibility is department scoped.
- Audit entries are append-only through the API.
- Filters are bounded; pagination has a hard maximum.
- Existing legacy tool-execution records remain readable.

## 4. Company Settings

### Settings

- Company name/logo, timezone, language and billing email.
- Department and member links.
- Data retention policy and default model.
- Notification and security policy.
- API-key configuration status (presence only; values are never returned).
- Export snapshot and reviewed deletion request.

### Cases

- Invalid timezone, model, URL, retention range or CIDR is rejected.
- Only Owner/Admin/CEO can mutate; destructive request is Owner-only.
- Export excludes password hashes and credential references.
- Deletion requires the exact workspace domain and creates audit/notifications.
- Settings update records before/after snapshots.

## 5. Notifications

### Event types

- `TASK_COMPLETED`, `TASK_FAILED`, `TASK_DUE_SOON`
- `WORKFLOW_FAILED`, `APPROVAL_REQUIRED`
- `AGENT_COST_LIMIT`
- `DOCUMENT_READY`, `INTEGRATION_DISCONNECTED`

### Delivery and preferences

- In-app is implemented as the durable source of truth.
- Email, Slack, Teams and mobile are preference/delivery states until a verified connector exists.
- Per-user enabled event types, channels and quiet hours.
- Deduplication prevents repeated scans from spamming the same event.
- Read-one, read-all, unread-count and preference APIs are tenant/user scoped.

## 6. Enterprise Integrations

### Supported catalog

Gmail, Outlook, Google Calendar, Slack, Teams, Google Drive, SharePoint,
CRM, Trello, Jira, Notion, PostgreSQL, Webhook and REST API.

### Connection model

- Provider, display name, auth type and non-secret credential reference.
- Explicit scopes, allowed resources and allowed AI-agent roles.
- Connection state, health check time/error and creator.
- Usage history containing operation, actor/agent, resource, result and latency.

### Cases

- No wildcard permission or default “all resources”.
- Duplicate connection names per provider/tenant are rejected.
- Connection tests validate configuration without exposing credentials.
- Disconnect is reversible and audited.
- Employees can view allowed integrations but cannot connect/disconnect.
- Agent execution must match both `allowed_agent_roles` and resource scopes.
- Outbound URL fetching is not performed by the registry, avoiding SSRF.

## 7. CI/CD

### CI

- Backend: PostgreSQL/pgvector + Redis services, Alembic to head, pytest.
- Frontend: Node 22, deterministic `npm ci`, ESLint, production build.
- Container validation: build backend and frontend images.
- Least-privilege workflow permissions and concurrency cancellation.

### CD

- Trigger only after successful CI on `main` or manual dispatch.
- Build immutable backend/frontend images.
- Tag by commit SHA and `latest`.
- Publish to GitHub Container Registry with provenance.
- No production deployment is attempted without an explicit environment target,
  credentials and rollback policy.

## 8. Acceptance criteria

- Migration upgrades from `b72e4f910c31` and downgrades cleanly.
- New APIs have RBAC and cross-tenant regression tests.
- Existing 64 backend tests continue to pass.
- Frontend lint and production build pass.
- Docker Compose validates and backend migration reaches the new head.
- Local and remote commit hashes match after push.
