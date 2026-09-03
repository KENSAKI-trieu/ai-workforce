from app.agents.base.agent import BaseAgent

agent = BaseAgent(
    role="HR",
    name="Human Resources AI",
    description="Answer governed HR questions and prepare HR workflows.",
    # Descriptive only: this list is rendered into the router prompt and the agent
    # catalogue, not enforced. Names that no longer exist anywhere in the backend were
    # dropped, because advertising them to the router invites it to pick this agent for
    # work nothing can carry out.
    capabilities=(
        "search_hr_policy",
        "get_employee_private_profile",
        "get_employee_contract_summary",
        "get_employee_compensation_summary",
        "get_employee_leave_summary",
        "get_employee_full_profile",
        "query_company_users_sql",
        "search_employee_profiles",
        "query_leave_balance",
        "create_leave_request",
        "create_onboarding_workflow",
        "get_contract_expiry",
        "list_pending_hr_approvals",
    ),
    system_prompt_key="system/human_resources",
)
