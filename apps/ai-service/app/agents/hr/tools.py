"""Tools the HR agent may be offered: the ceiling the backend's grant is
intersected with. Tools run in the backend; this only names them."""

# The HR capabilities, which the backend registers as gateway tools under the names its
# deterministic HR chat checks, so one grant switches a capability in both engines. Each
# runs that chat's own branch -- grant, scope, purpose limitation, audit -- and is terminal:
# the backend writes the reply, so a profile, a salary or a colleague's contact never comes
# back to the model. Only knowledge search returns something the model reads.
TOOLS: tuple[str, ...] = (
    "rag_search",
    "query_company_users_sql",
    "get_employee_full_profile",
    "get_employee_compensation_summary",
    "get_employee_private_profile",
    "get_employee_contract_summary",
    "query_leave_balance",
    "list_leave_requests",
    "request_leave",
    "cancel_leave_request",
    "get_contract_expiry",
    "list_pending_hr_approvals",
    "export_hr_directory",
    "create_onboarding_workflow",
)
