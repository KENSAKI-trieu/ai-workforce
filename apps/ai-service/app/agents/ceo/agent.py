"""Domain policy of the CEO agent.

Its answers are figures read from the other departments, so they are held to the tool
results like Finance's (numbers_from_tools). A question comparing two departments reads
one tool for each, hence more decisions per turn than the default.
"""

from app.agents.base.policy import DomainPolicy
from app.agents.ceo.prompts import DOMAIN_PROMPT
from app.agents.ceo.tools import TOOLS

POLICY = DomainPolicy(
    "CEO", TOOLS, DOMAIN_PROMPT, numbers_from_tools=True, max_model_iterations=6
)
