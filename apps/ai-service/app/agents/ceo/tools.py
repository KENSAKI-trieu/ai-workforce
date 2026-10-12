"""Tools the CEO agent may be offered: the ceiling the backend's grant is
intersected with. Tools run in the backend; this only names them.

Reading tools only, apart from assigning a task: the assistant gives the overview and
follows work up, and every department's writes stay with that department's own agent.
Each tool still checks the asker's own permissions, so the assistant shows a director
nothing their position does not already let them see.
"""

TOOLS: tuple[str, ...] = (
    "rag_search",
    # The overview and the decisions waiting.
    "executive_briefing",
    "list_my_approvals",
    # One department's detail.
    "compare_income_statements",
    "get_income_statement",
    "get_expense_breakdown",
    "get_account_trend",
    "budget_vs_actual",
    "ar_ap_aging",
    "payment_schedule",
    "list_contract_reviews",
    "list_marketing_campaigns",
    "expense_lookup",
    # Work handed out and followed up.
    "assign_task",
    "list_tasks",
)
