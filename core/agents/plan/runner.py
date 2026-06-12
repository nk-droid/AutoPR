import logging

from core.agents.plan.graph import build_plan_graph
import core.agents.plan.nodes as nodes
from core.agents.runner_logging import log_agent_decision
from core.orchestrator.models import StageStatus
from core.contracts.triage import TriageResult

logger = logging.getLogger(__name__)


class PlanAgent:
    def __init__(self):
        self.graph = build_plan_graph(nodes)

    def run(self, triage_result: TriageResult, repo_map: str = ""):
        result = self.graph.invoke(
            {
                "triage_result": triage_result,
                "repo_map": repo_map,
                "strategy": "",
                "steps": [],
                "assumptions": [],
                "open_questions": [],
                "status": StageStatus.OK,
                "final_output": {},
            }
        )

        log_agent_decision(logger, "plan", result["status"])
        return result["status"], result["final_output"]
