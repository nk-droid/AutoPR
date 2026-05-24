from typing import Any, TypedDict
from langgraph.graph import END, StateGraph
from core.orchestrator.models import StageStatus

class MergeState(TypedDict):
    context: dict[str, Any]
    status: StageStatus
    repository: str
    pull_request_number: int | None
    merge_method: str
    commit_title: str | None
    merge_result: dict[str, Any]
    notes: dict[str, Any]
    final_output: dict[str, Any]

def build_merge_graph(nodes) -> StateGraph[MergeState]:
    graph = StateGraph(MergeState)
    graph.add_node("prepare", nodes.prepare)
    graph.add_node("merge", nodes.merge)
    graph.add_node("finalize", nodes.finalize)

    graph.set_entry_point("prepare")

    graph.add_edge("prepare", "merge")
    graph.add_edge("merge", "finalize")
    graph.add_edge("finalize", END)
    
    return graph.compile()
