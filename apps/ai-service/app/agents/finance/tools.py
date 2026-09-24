"""Tools the Finance agent may be offered: the ceiling the backend's grant is
intersected with. Tools run in the backend; this only names them."""

TOOLS: tuple[str, ...] = (
    "rag_search",
    "expense_lookup",
    "create_task",
    "submit_approval_request",
)
