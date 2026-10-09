"""Graph of the Marketing agent: the standard agent graph."""

from langgraph.graph import StateGraph

from app.agents.base.graph import build_agent_graph
from app.agents.marketing.agent import POLICY


def build_graph() -> StateGraph:
    return build_agent_graph(POLICY)
