from core.agents.code.graph import build_code_graph
import core.agents.code.nodes as nodes
from core.contracts.enums import PlanStatus
from core.contracts.plan import PlanStep

class CodeAgent:
    def __init__(self):
        self.graph = build_code_graph(nodes)

    def run(
        self,
        step: PlanStep,
        repo_map: str,
        file_contents: dict[str, str],
        dependency_files: dict[str, str] | None = None,
        qa_feedback: str = "",
    ):
        result = self.graph.invoke({
            "step": step,
            "repo_map": repo_map,
            "file_contents": file_contents,
            "dependency_files": dependency_files or {},
            "qa_feedback": qa_feedback,
            "target_files": [],
            "files": {},
            "status": PlanStatus.OK,
            "notes": {},
            "final_output": None
        })

        return result["status"], result["final_output"]
