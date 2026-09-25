"""Graph of the HR agent: the standard agent graph, with no extra steps yet."""

from langgraph.graph import StateGraph

from app.agents.base.graph import build_agent_graph
from app.agents.hr.agent import POLICY


def build_graph() -> StateGraph:
    return build_agent_graph(POLICY)
