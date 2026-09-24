"""Default domain prompt of the Customer support (IT and Sales) agent.

A tenant's customisation arrives from the backend's plugin resolver in the request;
the technical rules of the decision node live in agents/base/decision.py.
"""

DOMAIN_PROMPT = "Ground customer replies in approved support material and gate outbound actions."
