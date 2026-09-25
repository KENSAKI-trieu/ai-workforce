"""Domain policy of the Knowledge agent."""

from app.agents.base.policy import DomainPolicy
from app.agents.knowledge.prompts import DOMAIN_PROMPT
from app.agents.knowledge.tools import TOOLS

POLICY = DomainPolicy("KNOWLEDGE", TOOLS, DOMAIN_PROMPT)
