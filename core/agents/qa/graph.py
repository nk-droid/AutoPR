from typing import Any, TypedDict
from langgraph.graph import END, StateGraph
from core.contracts.qa import QAOutput

class QAState(TypedDict):
    coding_output: dict[str, Any]
    coding_step: dict[str, Any]
    status: str
    summary: str
    checks: list[dict[str, Any]]
    notes: dict[str, Any]
    final_output: QAOutput

def build_qa_graph(nodes) -> StateGraph[QAState]:
    graph = StateGraph(QAState)
    graph.add_node("evaluate_inputs", nodes.evaluate_inputs)
    graph.add_node("run_checks", nodes.run_checks)
    graph.add_node("finalize", nodes.finalize)
    graph.set_entry_point("evaluate_inputs")
    graph.add_edge("evaluate_inputs", "run_checks")
    graph.add_edge("run_checks", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()
