from typing import Any, TypedDict
from langgraph.graph import END, StateGraph
from core.contracts.review import ReviewCheck
from core.contracts.run_context import PRToMergeContext
from core.orchestrator.models import StageStatus

class ReviewState(TypedDict):
    context: PRToMergeContext
    status: StageStatus
    summary: str
    checks: list[ReviewCheck]
    required_actions: list[str]
    notes: dict[str, Any]
    final_output: dict[str, Any]

def build_review_graph(nodes) -> StateGraph[ReviewState]:
    graph = StateGraph(ReviewState)
    graph.add_node("evaluate_review", nodes.evaluate_review)
    graph.add_node("finalize", nodes.finalize)

    graph.set_entry_point("evaluate_review")

    graph.add_edge("evaluate_review", "finalize")
    graph.add_edge("finalize", END)
    
    return graph.compile()
