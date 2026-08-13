# Giai đoạn 6: Persistence và Human-in-the-loop

## Checkpoint persistence

AI service dùng `langgraph-checkpoint-postgres` và gọi `PostgresSaver.setup()` trong application lifespan. Docker cấu hình:

```dotenv
LANGGRAPH_CHECKPOINT_BACKEND=postgres
LANGGRAPH_CHECKPOINT_AUTO_SETUP=true
LANGGRAPH_STRICT_MSGPACK=true
```

`LANGGRAPH_STRICT_MSGPACK=true` giới hạn kiểu dữ liệu được deserialize từ checkpoint. Development và test có thể dùng `memory`; ứng dụng từ chối khởi động graph bằng memory ở `staging` hoặc `production`.

`thread_id` chính là UUID của `ChatConversation`. Backend lưu cùng giá trị vào:

- `chat_conversations.thread_id`;
- `agent_workflows.thread_id`;
- LangGraph `config.configurable.thread_id`;
- approval payload `thread_id`.

Mỗi conversation graph có một `AgentWorkflow`. `workflow_id` được backend tạo trước khi gọi AI service, đưa vào trusted runtime context, checkpoint state và audit metadata của tool. Resume kiểm tra lại tenant, initiator, conversation và workflow trước khi đọc checkpoint.

## Approval flow

```text
Backend creates/reuses AgentWorkflow and commits it
  -> graph selects WRITE/EXTERNAL_ACTION tool
  -> AI service registers approval through internal backend endpoint
  -> backend creates WorkflowApproval idempotently by interrupt_id
  -> graph calls interrupt() and PostgresSaver stores the checkpoint
  -> executive/manager records a decision
  -> backend resumes with the same conversation_id/thread_id
  -> graph rechecks current ACL and executes or rejects the action
  -> backend updates workflow, approval, audit and chat history
```

Approval registration happens before `interrupt()`. Because LangGraph restarts the node on resume, the registration uses a PostgreSQL advisory lock and unique `langgraph_interrupt_id`; rerunning the node returns the same approval instead of creating another record.

## Approval policy

- `Owner`, `Admin` and `CEO`: approve all LangGraph action levels in the tenant.
- `Manager`: approve non-`CRITICAL` actions only for a direct report or the same department.
- A Manager cannot approve their own action.
- Other roles cannot approve LangGraph actions.
- Tool arguments cannot be edited during approval.

The approval row is locked with `SELECT ... FOR UPDATE` while a decision is processed, preventing concurrent approve/reject requests from resuming one checkpoint twice.

## Failure and retry

The backend commits the human decision before the remote resume call. If AI service is unavailable:

- approval remains `APPROVED` or `REJECTED`;
- workflow becomes `RESUME_FAILED`;
- `resume_error` records the failure;
- repeating the same approval action retries resume;
- a different or duplicate decision still returns `409`.

Successful resume records `resumed_at`. Action tools keep their deterministic idempotency key, so a retry cannot duplicate a backend side effect.

## Database changes

Migration `j75e2a9c4f10` adds:

- unique conversation/thread mapping;
- workflow tenant/thread index;
- `workflow_approvals.langgraph_interrupt_id` unique constraint;
- `workflow_approvals.resumed_at`;
- `workflow_approvals.resume_error`.

LangGraph owns its checkpoint tables through `PostgresSaver.setup()`. Add a retention job for completed threads according to the organization's audit policy; do not delete checkpoints for pending or `RESUME_FAILED` workflows.

The design follows the official [LangGraph persistence guide](https://docs.langchain.com/oss/python/langgraph/persistence) for durable thread-scoped checkpoints and the [interrupt contract](https://docs.langchain.com/oss/python/langgraph/interrupts), including reuse of the same `thread_id`, `Command(resume=...)`, and idempotent work before `interrupt()`.
