from app.agents.base.agent import BaseAgent

agent = BaseAgent(
    role="SOFTWARE_ENGINEER",
    name="Software Engineer AI",
    description="Diagnose and implement software changes with verification.",
    capabilities=("analyze_code", "propose_patch", "review_tests"),
)
