from langgraph.graph import StateGraph, END
from typing import TypedDict, Any, Dict, List

from core.contracts.code import CodeOutput

class CodeState(TypedDict):
    step: dict[str, Any]
    repo_map: str
    file_contents: Dict[str, str]
    target_files: List[str]
    files: Dict[str, str]
    status: str
    notes: dict
    final_output: CodeOutput

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
