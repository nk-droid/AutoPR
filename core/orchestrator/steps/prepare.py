from pathlib import Path
from typing import Any

from core.contracts.enums import PipelineStage
from core.orchestrator.models import RunModel, StageResult, StageStatus
from core.orchestrator.steps.base import PipelineStep, StepRuntime

from infra.repo_worker.workspace import build_repo_map, clone_repo

from observability.tracing import pipeline_step_attrs, traced

class PrepareStep(PipelineStep):
    """Clone the target repo once and seed repo_path and repo_map.

    Runs before planning so the planner can target files that actually exist in
    the repo, and so QA later materializes generated files on top of the real
    repo instead of an empty workspace.
    """

    stage = PipelineStage.PREPARE

    @traced("pipeline.prepare_step", attributes=pipeline_step_attrs)
    def execute(self, context: dict[str, Any], run: RunModel, runtime: StepRuntime) -> StageResult:
        # Idempotent: reuse an existing checkout (e.g. on a QA retry).
        existing = context.get("repo_path")
        if isinstance(existing, str) and existing and Path(existing).is_dir():
            return StageResult(stage=self.stage, status=StageStatus.OK, outputs={})

        repository = context.get("repository") or run.repository
        if not repository:
            return StageResult(
                stage=self.stage,
                status=StageStatus.BLOCKED,
                notes={"reason": "Prepare blocked: repository missing."},
            )

        base_branch = context.get("base_branch") or "main"
        try:
            repo_path = clone_repo(
                repository,
                base_branch,
                clone_url=context.get("repository_clone_url"),
                token=context.get("github_token"),
            )
        except Exception as exc:
            return StageResult(
                stage=self.stage,
                status=StageStatus.BLOCKED,
                notes={"reason": f"Prepare blocked: clone failed ({exc})."},
            )

        return StageResult(
            stage=self.stage,
            status=StageStatus.OK,
            outputs={
                "repo_path": str(repo_path),
                "repo_map": build_repo_map(repo_path),
            },
        )
