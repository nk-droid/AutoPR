import logging
from typing import Any

import core.agents.publish.nodes as nodes
from core.agents.publish.graph import build_publish_graph
from core.agents.runner_logging import log_agent_decision
from core.orchestrator.models import StageStatus

logger = logging.getLogger(__name__)


class PublishAgent:
    def __init__(self):
        self.graph = build_publish_graph(nodes)

    def run(self, context: dict[str, Any]):
        result = self.graph.invoke(
            {
                "context": context,
                "status": StageStatus.OK,
                "repository": "",
                "base_branch": "",
                "head_branch": "",
                "commit_message": "",
                "remote": "origin",
                "files_payload": {},
                "git": None,
                "workspace_path": "",
                "used_temp_workspace": False,
                "written_files": [],
                "commit_output": "",
                "push_output": "",
                "head_sha": "",
                "pr_auth_source": "environment_or_context",
                "notes": {},
                "final_output": {},
            }
        )

        log_agent_decision(logger, "publish", result["status"])
        return result["status"], result["final_output"]
