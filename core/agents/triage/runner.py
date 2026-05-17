import core.agents.triage.nodes as nodes
from core.agents.triage.graph import build_triage_graph
from core.contracts.enums import AmbiguityStatus, RiskLevel
from core.contracts.run_context import TriageIssueInput
from core.contracts.triage import AmbiguityResult, Risk, TaskSpec

class TriageAgent:
    def __init__(self):
        self.graph = build_triage_graph(nodes)

    def run(self, issue: TriageIssueInput):
        result = self.graph.invoke({
            "issue": issue,
            "task_spec": TaskSpec(problem="", acceptance_criteria=[], constraints=[], out_of_scope=[]),
            "risk": Risk(level=RiskLevel.LOW, reasons=[]),
            "ambiguity": AmbiguityResult(status=AmbiguityStatus.OK, questions=[]),
            "status": AmbiguityStatus.OK,
            "final_output": {},
        })

        return result["status"], result["final_output"]
