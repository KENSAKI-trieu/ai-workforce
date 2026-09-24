"""Graph of the Legal agent: the standard agent graph, with no extra steps yet."""

from langgraph.graph import StateGraph

from app.agents.base.graph import build_agent_graph
from app.agents.legal.agent import POLICY


def build_graph() -> StateGraph:
    return build_agent_graph(POLICY)
