from langgraph.graph import StateGraph, END
from typing import TypedDict, Any

from core.contracts.run_context import TriageIssueInput
from core.contracts.triage import TaskSpec, Risk, AmbiguityResult
from core.agents.plan.graph import PARSER_RETRY_POLICY
from core.orchestrator.models import StageStatus

class TriageState(TypedDict):
    issue: TriageIssueInput
    task_spec: TaskSpec
    risk: Risk
    ambiguity: AmbiguityResult
    status: StageStatus
    final_output: dict[str, Any]

# Extract task -> Assess risk -> Detect ambiguity -> Finalize
def build_triage_graph(nodes) -> StateGraph[TriageState]:
    graph = StateGraph(TriageState)

    graph.add_node("extract_task", nodes.extract_task, retry_policy=PARSER_RETRY_POLICY)
    graph.add_node("assess_risk", nodes.assess_risk, retry_policy=PARSER_RETRY_POLICY)
    graph.add_node("detect_ambiguity", nodes.detect_ambiguity, retry_policy=PARSER_RETRY_POLICY)
    graph.add_node("finalize", nodes.finalize, retry_policy=PARSER_RETRY_POLICY)

    graph.set_entry_point("extract_task")

    graph.add_edge("extract_task", "assess_risk")
    graph.add_edge("assess_risk", "detect_ambiguity")
    graph.add_edge("detect_ambiguity", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile()