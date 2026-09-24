"""Default domain prompt of the CEO agent.

A tenant's customisation arrives from the backend's plugin resolver in the request;
the technical rules of the decision node live in agents/base/decision.py.
"""

# Later the coordinating agent: it will delegate to the other agents as tools inside
# its own graph.
DOMAIN_PROMPT = "Coordinate cross-domain work, preserve domain ACLs, and gate every action."
