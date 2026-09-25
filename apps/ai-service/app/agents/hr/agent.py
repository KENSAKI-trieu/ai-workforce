"""Domain policy of the HR agent."""

from app.agents.base.policy import DomainPolicy
from app.agents.hr.prompts import DOMAIN_PROMPT
from app.agents.hr.tools import TOOLS

POLICY = DomainPolicy("HR", TOOLS, DOMAIN_PROMPT)
