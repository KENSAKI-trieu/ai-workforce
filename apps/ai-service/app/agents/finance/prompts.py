"""Default domain prompt of the Finance agent.

A tenant's customisation arrives from the backend's plugin resolver in the request;
the technical rules of the decision node live in agents/base/decision.py.
"""

DOMAIN_PROMPT = "Use tenant cost data and finance policy; do not infer missing amounts."
