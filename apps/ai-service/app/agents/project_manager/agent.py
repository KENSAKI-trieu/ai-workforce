from app.agents.base.agent import BaseAgent

agent = BaseAgent(
    role="PROJECT_MANAGER",
    name="Project Manager AI",
    description="Plan tasks, dependencies, risks and delivery milestones.",
    capabilities=("plan_project", "prioritize_tasks", "summarize_progress"),
)
