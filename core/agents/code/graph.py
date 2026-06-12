from langgraph.graph import StateGraph, END
from typing import TypedDict, Dict, List

from core.contracts.code import CodeOutput
from core.contracts.plan import PlanStep

from core.orchestrator.models import StageStatus

class CodeState(TypedDict):
    step: PlanStep
    repo_map: str
    file_contents: Dict[str, str]
    dependency_files: Dict[str, str]
    qa_feedback: str
    target_files: List[str]
    files: Dict[str, str]
    status: StageStatus
    notes: dict
    final_output: CodeOutput

# Understand task -> Locate files -> Generate patch -> Validate patch -> Finalize
def build_code_graph(nodes) -> StateGraph[CodeState]:
    graph = StateGraph(CodeState)

    graph.add_node("understand_task", nodes.understand_task)
    graph.add_node("locate_files", nodes.locate_files)
    graph.add_node("generate_patch", nodes.generate_patch)
    graph.add_node("validate_patch", nodes.validate_patch)
    graph.add_node("finalize", nodes.finalize)

    graph.set_entry_point("understand_task")

    graph.add_edge("understand_task", "locate_files")
    graph.add_edge("locate_files", "generate_patch")
    graph.add_edge("generate_patch", "validate_patch")
    graph.add_edge("validate_patch", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile()
