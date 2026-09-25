"""Default domain prompt of the HR agent.

A tenant's customisation arrives from the backend's plugin resolver in the request;
the technical rules of the decision node live in agents/base/decision.py.
"""

DOMAIN_PROMPT = "Apply purpose limitation and employee-scope policy before using HR data."
