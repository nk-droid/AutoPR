import logging
from typing import Any

import core.agents.merge.nodes as nodes
from core.agents.merge.graph import build_merge_graph
from core.agents.runner_logging import log_agent_decision
from core.orchestrator.models import StageStatus

logger = logging.getLogger(__name__)

class MergeAgent:
    def __init__(self):
        self.graph = build_merge_graph(nodes)

    def run(self, context: dict[str, Any]):
        result = self.graph.invoke(
            {
                "context": context,
                "status": StageStatus.OK,
                "repository": "",
                "pull_request_number": None,
                "merge_method": "squash",
                "commit_title": None,
                "merge_result": {},
                "notes": {},
                "final_output": {},
            }
        )

        log_agent_decision(logger, "merge", result["status"])
        return result["status"], result["final_output"]
