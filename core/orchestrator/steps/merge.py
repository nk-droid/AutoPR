import os
from typing import Any

from core.contracts.enums import PipelineStage, RunState
from core.orchestrator.models import RunModel, StageResult, StageStatus

from infra.github.client import GitHubClient

from core.orchestrator.steps.base import PipelineStep, StepRuntime, is_success_status

class MergeStep(PipelineStep):
    stage = PipelineStage.MERGE

    def execute(self, context: dict[str, Any], run: RunModel, runtime: StepRuntime) -> StageResult:
        merge_decision = context.get("_merge_decision")
        if isinstance(merge_decision, dict) and not bool(merge_decision.get("allowed", True)):
            return StageResult(
                stage=self.stage,
                status=StageStatus.BLOCKED,
                notes={
                    "reason": str(merge_decision.get("reason", "Merge decision blocked")),
                    "blocking_reasons": merge_decision.get("blocking_reasons", []),
                },
            )

        repository = str(context.get("repository", "")).strip()
        raw_pr_number = context.get("pull_request_number")
        if raw_pr_number is None:
            raw_pr_number = run.pull_request_number
        pull_request_number: int | None = None
        if isinstance(raw_pr_number, int):
            pull_request_number = raw_pr_number
        elif isinstance(raw_pr_number, str) and raw_pr_number.strip().isdigit():
            pull_request_number = int(raw_pr_number.strip())

        if not repository or pull_request_number is None:
            return StageResult(
                stage=self.stage,
                status=StageStatus.BLOCKED,
                outputs={
                    "merge_output": {
                        "status": "blocked",
                        "merged": False,
                        "message": "Merge blocked: repository or pull_request_number missing.",
                        "merge_sha": "",
                    }
                },
                notes={"blocking_reason": "missing_merge_inputs"},
            )

        execute_remote = bool(context.get("execute_remote_actions", False))
        if not execute_remote:
            return StageResult(
                stage=self.stage,
                status=StageStatus.NEEDS_REVIEW,
                outputs={
                    "merge_output": {
                        "status": "needs_review",
                        "merged": False,
                        "message": "Merge skipped: execute_remote_actions is False.",
                        "merge_sha": "",
                    }
                },
                notes={"blocking_reason": "remote_execution_disabled"},
            )

        merge_method = str(context.get("merge_method", "squash")).strip() or "squash"
        commit_title = context.get("merge_commit_title")
        client = GitHubClient(token=context.get("github_token") or os.environ.get("GITHUB_TOKEN"))

        try:
            payload = client.merge_pull_request(
                repo=repository,
                pull_number=pull_request_number,
                merge_method=merge_method,
                commit_title=commit_title if isinstance(commit_title, str) and commit_title.strip() else None,
            )
            merged = bool(payload.get("merged"))
            merge_result = {
                "status": "ok" if merged else "blocked",
                "merged": merged,
                "message": str(payload.get("message", "")).strip(),
                "merge_sha": str(payload.get("sha", "")).strip(),
            }
            status = StageStatus.OK if merged else StageStatus.BLOCKED
        except Exception as exc:
            merge_result = {
                "status": "blocked",
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
