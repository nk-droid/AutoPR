from typing import Any, TypedDict
from langgraph.graph import END, StateGraph
from core.orchestrator.models import StageStatus
from infra.repo_worker.git_utils import GitService


class PublishState(TypedDict):
    context: dict[str, Any]
    status: StageStatus
    repository: str
    base_branch: str
    head_branch: str
    commit_message: str
    remote: str
    files_payload: dict[str, str]
    git: GitService | None
    workspace_path: str
    used_temp_workspace: bool
    written_files: list[str]
    commit_output: str
    push_output: str
    head_sha: str
    pr_auth_source: str
    notes: dict[str, Any]
    final_output: dict[str, Any]


# Prepare -> Resolve workspace -> Apply files -> Commit & push -> Finalize
def build_publish_graph(nodes) -> StateGraph[PublishState]:
    graph = StateGraph(PublishState)
    graph.add_node("prepare", nodes.prepare)
    graph.add_node("resolve_workspace", nodes.resolve_workspace)
    graph.add_node("apply_files", nodes.apply_files)
    graph.add_node("commit_push", nodes.commit_push)
    graph.add_node("finalize", nodes.finalize)

    graph.set_entry_point("prepare")

    graph.add_edge("prepare", "resolve_workspace")
    graph.add_edge("resolve_workspace", "apply_files")
    graph.add_edge("apply_files", "commit_push")
    graph.add_edge("commit_push", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile()
