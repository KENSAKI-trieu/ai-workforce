"""Domain policy of the Marketing agent."""

from app.agents.base.policy import DomainPolicy
from app.agents.marketing.prompts import DOMAIN_PROMPT
from app.agents.marketing.tools import TOOLS

POLICY = DomainPolicy("MARKETING", TOOLS, DOMAIN_PROMPT)
