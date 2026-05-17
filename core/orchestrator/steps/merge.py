import os
from typing import Any

from core.contracts.enums import PipelineStage, RunState
from core.orchestrator.models import RunModel, StageResult, StageStatus

from infra.github.client import GitHubClient

from core.orchestrator.steps.base import PipelineStep, StepRuntime, is_success_status

class MergeStep(PipelineStep):
    stage = PipelineStage.MERGE

    @staticmethod
    def _normalize_text(value: Any) -> str:
        return value.strip() if isinstance(value, str) else ""

    def execute(self, context: dict[str, Any], run: RunModel, runtime: StepRuntime) -> StageResult:
        merge_decision = context.get("_merge_decision")
        # Respect upstream policy decision before calling merge API.
        if isinstance(merge_decision, dict) and not bool(merge_decision.get("allowed", True)):
            reason_value = merge_decision.get("reason")
            reason = reason_value if isinstance(reason_value, str) and reason_value else "Merge decision blocked"
            blocking_reasons = merge_decision.get("blocking_reasons", [])
            return StageResult(
                stage=self.stage,
                status=StageStatus.BLOCKED,
                notes={
                    "reason": reason,
                    "blocking_reasons": blocking_reasons if isinstance(blocking_reasons, list) else [],
                },
            )

        repository = self._normalize_text(context.get("repository"))
        raw_pr_number = context.get("pull_request_number")
        if raw_pr_number is None:
            raw_pr_number = run.pull_request_number
        pull_request_number = raw_pr_number if isinstance(raw_pr_number, int) else None

        if not repository or pull_request_number is None:
            return StageResult(
                stage=self.stage,
                status=StageStatus.BLOCKED,
                outputs={
                    "merge_output": {
                        "status": StageStatus.BLOCKED.value,
                        "merged": False,
                        "message": "Merge blocked: repository or pull_request_number missing.",
                        "merge_sha": "",
                    }
                },
                notes={"blocking_reason": "missing_merge_inputs"},
            )

        execute_remote = bool(context.get("execute_remote_actions", False))
        if not execute_remote:
            # Keep merge explicit unless remote actions are intentionally enabled.
            return StageResult(
                stage=self.stage,
                status=StageStatus.NEEDS_REVIEW,
                outputs={
                    "merge_output": {
                        "status": StageStatus.NEEDS_REVIEW.value,
                        "merged": False,
                        "message": "Merge skipped: execute_remote_actions is False.",
                        "merge_sha": "",
                    }
                },
                notes={"blocking_reason": "remote_execution_disabled"},
            )

        merge_method = self._normalize_text(context.get("merge_method")) or "squash"
        commit_title = context.get("merge_commit_title")
        client = GitHubClient(token=context.get("github_token") or os.environ.get("GITHUB_TOKEN"))

        try:
            payload = client.merge_pull_request(
                repo=repository,
                pull_number=pull_request_number,
                merge_method=merge_method,
                commit_title=self._normalize_text(commit_title) or None,
            )
            merged = bool(payload.get("merged"))
            message_value = payload.get("message")
            sha_value = payload.get("sha")
            merge_result = {
                "status": StageStatus.OK.value if merged else StageStatus.BLOCKED.value,
                "merged": merged,
                "message": self._normalize_text(message_value),
                "merge_sha": self._normalize_text(sha_value),
            }
            status = StageStatus.OK if merged else StageStatus.BLOCKED
        except Exception as exc:
            merge_result = {
                "status": StageStatus.BLOCKED.value,
                "merged": False,
                "message": "Merge failed.",
                "merge_sha": "",
                "error": str(exc),
            }
            status = StageStatus.BLOCKED
        finally:
            client.close()

        return StageResult(
            stage=self.stage,
            status=status,
            outputs={"merge_output": merge_result},
            notes={"merge_method": merge_method},
        )

    def after(
        self,
        result: StageResult,
        context: dict[str, Any],
        run: RunModel,
    ) -> list[tuple[str, str]]:
        if is_success_status(result.status):
            return [(RunState.MERGED.value, "merge completed")]
        return []
