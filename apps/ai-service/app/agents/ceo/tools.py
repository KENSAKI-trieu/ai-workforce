"""Tools the CEO agent may be offered: the ceiling the backend's grant is
intersected with. Tools run in the backend; this only names them."""

# `employee_lookup` and `leave_lookup` are listed here but withheld from CEO by the backend's
# gateway grants: their `purpose` argument widens the HR sections released, and on this
# path the model would choose it. Granting them is an explicit tenant decision.
TOOLS: tuple[str, ...] = (
    "rag_search",
    "employee_lookup",
    "leave_lookup",
    "expense_lookup",
    "create_task",
    "generate_legal_document",
    "submit_approval_request",
)
