from typing import Any, TypedDict
from langgraph.graph import END, StateGraph
from core.contracts.review import ReviewOutput

class ReviewState(TypedDict):
    context: dict[str, Any]
    status: str
    summary: str
    checks: list[dict[str, Any]]
    required_actions: list[str]
    notes: dict[str, Any]
    final_output: ReviewOutput

def build_review_graph(nodes) -> StateGraph[ReviewState]:
    graph = StateGraph(ReviewState)
    graph.add_node("evaluate_review", nodes.evaluate_review)
    graph.add_node("finalize", nodes.finalize)
    graph.set_entry_point("evaluate_review")
    graph.add_edge("evaluate_review", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()
