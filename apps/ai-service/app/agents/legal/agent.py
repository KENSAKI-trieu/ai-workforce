"""Domain policy of the Legal agent."""

from app.agents.base.policy import DomainPolicy
from app.agents.legal.prompts import DOMAIN_PROMPT
from app.agents.legal.tools import TOOLS

POLICY = DomainPolicy("LEGAL", TOOLS, DOMAIN_PROMPT)
