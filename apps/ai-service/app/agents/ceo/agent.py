"""Domain policy of the CEO agent."""

from app.agents.base.policy import DomainPolicy
from app.agents.ceo.prompts import DOMAIN_PROMPT
from app.agents.ceo.tools import TOOLS

POLICY = DomainPolicy("CEO", TOOLS, DOMAIN_PROMPT)
