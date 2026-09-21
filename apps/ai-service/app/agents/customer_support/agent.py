from app.agents.base.agent import BaseAgent

agent = BaseAgent(
    role="CUSTOMER_SUPPORT",
    name="Customer Support AI",
    description="Triage and answer customer support requests.",
    capabilities=("search_knowledge", "classify_ticket", "draft_response"),
)
