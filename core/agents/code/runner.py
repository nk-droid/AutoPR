import logging

from core.agents.code.graph import build_code_graph
import core.agents.code.nodes as nodes
from core.agents.runner_logging import log_agent_decision
from core.orchestrator.models import StageStatus
from core.contracts.plan import PlanStep

logger = logging.getLogger(__name__)


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
        result = self.graph.invoke(
            {
                "step": step,
                "repo_map": repo_map,
                "file_contents": file_contents,
                "dependency_files": dependency_files or {},
                "qa_feedback": qa_feedback,
                "target_files": [],
                "files": {},
                "status": StageStatus.OK,
                "notes": {},
                "final_output": None,
            }
        )

        log_agent_decision(logger, "code", result["status"], step_id=getattr(step, "id", None))
        return result["status"], result["final_output"]
