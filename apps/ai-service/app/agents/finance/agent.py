from app.agents.base.agent import BaseAgent

agent = BaseAgent(
    role="FINANCE",
    name="Finance AI",
    description="Analyze invoices, budgets and finance policies.",
    capabilities=("search_finance_policy", "extract_invoice", "reconcile_purchase_order"),
)
