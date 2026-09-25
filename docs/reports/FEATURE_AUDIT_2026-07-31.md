# Enterprise Feature Audit — 2026-07-31

## Completed fixes

### Workspace, employees and RBAC

- Public registration always creates an isolated workspace and an `Owner`.
- Public callers can no longer choose a privileged role or join the first tenant.
- Locked accounts are rejected even when they still hold an old access/refresh token.
- Dynamic departments support create, update, deactivate and safe delete.
- Owner/Admin/Manager/Employee scopes are enforced for employee and task data.
- Self-lock, last-owner removal and Admin-over-Owner changes are blocked.
- Employees cannot change their own department through the profile endpoint.

### AI Employees

- Agent responses expose tools, allowed/disallowed actions and knowledge collections.
- Owner/Admin can update prompt, model, tools, actions, collections and active state.
- The execution engine now rejects inactive agents and enforces tool/action policies.
- Agent history, metered cost and workflow success rate are available through the stats API.
- The agent page now surfaces configuration, cost, success rate and persistent chat.

### Tasks

- Tenant/department/employee visibility rules are enforced.
- Assignee and AI Agent ownership are validated against the workspace.
- Status transitions are validated; terminal states cannot be reopened arbitrarily.
- Overdue status is derived from due date.
- Create/update/comment actions are written to an auditable history.
- Only draft tasks can be deleted; active tasks must be cancelled.

### Workflow and approval

- Workflow definitions support Trigger, Condition, AI Agent, Tool, Human Approval,
  Delay, Loop, Notification and Output node types.
- DAG node IDs and edges are validated.
- Manual runs and per-definition run history are persisted.
- Human Approval nodes create real pending approval records.
- Approval listing and actions are tenant-safe and approver-safe.
- Approve, Reject and Edit-and-approve are supported with risk, source and reason data.
- Expired approvals cannot be processed.

### Knowledge Base and chat

- PDF, DOCX, TXT, Markdown and CSV upload/extraction are supported.
- Public website import is supported with private/reserved-network SSRF protection,
  redirect validation and download limits.
- Collections, department ACL, chunking, embeddings, citations, metadata update and
  delete are supported.
- Chat persists conversations and messages, citations, executed tools and feedback.
- Regenerate, copy, workspace sharing, Markdown export and create-task-from-chat are supported.

## Verified

- Backend: `64 passed`.
- Frontend ESLint: `0 errors`.
- Next.js production build: successful.
- Alembic: `b72e4f910c31 (head)`.
- Docker: PostgreSQL healthy; backend, frontend and Redis running.
- HTTP smoke tests: frontend feature pages and protected APIs return `200`.
- Website import SSRF test returns `422` for localhost/private network targets.

## Remaining product integrations

These require external credentials and product decisions rather than a local bug fix:

- Google Drive and SharePoint OAuth sync, incremental updates and webhook lifecycle.
- Real CRM, email, ticketing and payment/refund connectors.
- Durable binary storage for task/chat attachments (S3-compatible storage is recommended).
- A production workflow worker/queue for executing Condition, Delay and Loop nodes over time.
- PDF chat export; current export is Markdown.

The local APIs intentionally do not pretend these external actions succeeded when no
connector or credential is configured.
