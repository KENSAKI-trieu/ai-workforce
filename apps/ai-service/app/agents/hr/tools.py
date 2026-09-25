"""Tools the HR agent may be offered: the ceiling the backend's grant is
intersected with. Tools run in the backend; this only names them."""

# HR does not reach this graph yet: the backend routes that role to its own deterministic
# executor. The names below are gateway tool names, while an HR agent's tools_access holds
# capability names from backend/app/core/hr_capabilities.py, and the two sets are disjoint
# -- the tool scope intersects them, so routing HR here today would hand the model an empty
# toolset. The backend grants HR no gateway tools on purpose (gateway_tools.py); HR tools
# must exist in the backend registry, with server-side section checks, before HR moves.
TOOLS: tuple[str, ...] = (
    "rag_search",
    "employee_lookup",
    "leave_lookup",
    "create_task",
    "submit_approval_request",
)
