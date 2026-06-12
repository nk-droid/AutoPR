import logging

from core.agents.pr.graph import build_pr_graph
import core.agents.pr.nodes as nodes
from core.agents.runner_logging import log_agent_decision
from core.contracts.run_context import IssueToPRContext
from core.orchestrator.models import StageStatus

logger = logging.getLogger(__name__)


class PRAgent:
    def __init__(self):
        self.graph = build_pr_graph(nodes)

    def run(self, context: IssueToPRContext):
        result = self.graph.invoke(
            {
                "context": context,
                "status": StageStatus.OK,
                "request": None,
                "pull_request_number": None,
                "pull_request_url": "",
                "summary": "",
                "notes": {},
                "final_output": {},
            }
        )

        log_agent_decision(logger, "pr", result["status"])
        return result["status"], result["final_output"]
