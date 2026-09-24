"""Domain policy of the Customer support (IT and Sales) agent."""

from app.agents.base.policy import DomainPolicy
from app.agents.customer_support.prompts import DOMAIN_PROMPT
from app.agents.customer_support.tools import TOOLS

POLICY = DomainPolicy("CUSTOMER_SUPPORT", TOOLS, DOMAIN_PROMPT)
