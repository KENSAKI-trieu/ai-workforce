"""Default domain prompt of the Knowledge agent.

A tenant's customisation arrives from the backend's plugin resolver in the request;
the technical rules of the decision node live in agents/base/decision.py.
"""

DOMAIN_PROMPT = "Answer only from governed tenant knowledge and provide verifiable citations."
