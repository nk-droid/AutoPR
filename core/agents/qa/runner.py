from core.agents.qa.graph import build_qa_graph
import core.agents.qa.nodes as nodes
from core.contracts.code import CodeOutput
from core.contracts.plan import PlanStep
from core.contracts.run_context import ToolRunResult
from core.orchestrator.models import StageStatus

class QAAgent:
    def __init__(self):
        self.graph = build_qa_graph(nodes)

    def run(self, coding_output: CodeOutput, coding_step: PlanStep, tool_results: list[ToolRunResult]):
        result = self.graph.invoke(
            {
                "coding_output": coding_output,
                "coding_step": coding_step,
                "tool_results": tool_results,
                "status": StageStatus.OK,
                "summary": "",
                "checks": [],
                "notes": {},
                "final_output": {},
            }
        )
        return result["status"], result["final_output"]
