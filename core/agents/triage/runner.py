import logging

import core.agents.triage.nodes as nodes
from core.agents.triage.graph import build_triage_graph
from core.agents.runner_logging import log_agent_decision
from core.contracts.enums import RiskLevel
from core.orchestrator.models import StageStatus
from core.contracts.run_context import TriageIssueInput
from core.contracts.triage import AmbiguityResult, Risk, TaskSpec

logger = logging.getLogger(__name__)

class TriageAgent:
    def __init__(self):
        self.graph = build_triage_graph(nodes)

    def run(self, issue: TriageIssueInput):
        result = self.graph.invoke({
            "issue": issue,
            "task_spec": TaskSpec(problem="", acceptance_criteria=[], constraints=[], out_of_scope=[]),
            "risk": Risk(level=RiskLevel.LOW, reasons=[]),
            "ambiguity": AmbiguityResult(status=StageStatus.OK, questions=[]),
            "status": StageStatus.OK,
            "final_output": {},
        })

        log_agent_decision(logger, "triage", result["status"])
        return result["status"], result["final_output"]
