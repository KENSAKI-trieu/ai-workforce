"""Domain policy of the Finance agent."""

from app.agents.base.policy import DomainPolicy
from app.agents.finance.prompts import DOMAIN_PROMPT
from app.agents.finance.tools import TOOLS

POLICY = DomainPolicy("FINANCE", TOOLS, DOMAIN_PROMPT)
