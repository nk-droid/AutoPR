from typing import Any, TypedDict
from langgraph.graph import END, StateGraph
from core.contracts.code import CodeOutput
from core.contracts.plan import PlanStep
from core.contracts.qa import QACheck
from core.contracts.run_context import ToolRunResult
from core.orchestrator.models import StageStatus


class QAState(TypedDict):
    coding_output: CodeOutput
    coding_step: PlanStep
    tool_results: list[ToolRunResult]
    status: StageStatus
    summary: str
    checks: list[QACheck]
    notes: dict[str, Any]
    final_output: dict[str, Any]


# Evaluate inputs -> Run checks -> Finalize
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
