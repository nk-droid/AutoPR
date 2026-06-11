from typing import Any, TypedDict
from langgraph.graph import END, StateGraph
from core.contracts.pr import PROpenRequest
from core.contracts.run_context import IssueToPRContext
from core.orchestrator.models import StageStatus

class PRState(TypedDict):
    context: IssueToPRContext
    status: StageStatus
    request: PROpenRequest | None
    pull_request_number: int | None
    pull_request_url: str
    summary: str
    notes: dict[str, Any]
    final_output: dict[str, Any]

# Prepare request -> Open PR -> Finalize
def build_pr_graph(nodes) -> StateGraph[PRState]:
    graph = StateGraph(PRState)
    graph.add_node("prepare_request", nodes.prepare_request)
    graph.add_node("open_pr", nodes.open_pr)
    graph.add_node("finalize", nodes.finalize)

    graph.set_entry_point("prepare_request")
    
    graph.add_edge("prepare_request", "open_pr")
    graph.add_edge("open_pr", "finalize")
    graph.add_edge("finalize", END)
    
    return graph.compile()
