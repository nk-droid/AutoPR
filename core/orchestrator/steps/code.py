import uuid
from typing import Any

import ray

from core.contracts.code import CodeOutput as CodeOutputModel
from core.contracts.enums import PipelineStage, RunState
from core.contracts.plan import PlanStep as PlanStepModel
from core.contracts.run_context import CodeWorkerInput
from core.orchestrator.models import RunModel, StageResult, StageStatus
from core.orchestrator.steps.base import PipelineStep, StepRuntime, is_success_status

from infra.ray.actors import CodeWorker
from infra.repo_worker.workspace import read_target_files

from observability.tracing import inject_trace_context, pipeline_step_attrs, traced


def _dependency_levels(steps: list[PlanStepModel]) -> list[list[PlanStepModel]] | None:
    """
    Returns a list of levels of steps, where each level can be executed in parallel and
    all dependencies of a level are in previous levels. If there is a cycle in the dependencies,
    returns None.

    Args:
        steps: A list of PlanStepModel, each with a unique id and a list of dependencies (by id).

    Returns:
        A list of levels of steps, or None if there is a cycle.
    """

    index_of = {step.id: position for position, step in enumerate(steps)}
    by_id = {step.id: step for step in steps}
    indegree = {step.id: 0 for step in steps}
    dependents: dict[uuid.UUID, list[uuid.UUID]] = {step.id: [] for step in steps}

    for step in steps:
        for dependency in step.dependencies:
            if dependency in by_id and dependency != step.id:
                dependents[dependency].append(step.id)
                indegree[step.id] += 1

    current = sorted(
        (step_id for step_id, degree in indegree.items() if degree == 0),
        key=lambda step_id: index_of[step_id],
    )

    levels: list[list[PlanStepModel]] = []
    processed = 0
    while current:
        levels.append([by_id[step_id] for step_id in current])
        processed += len(current)

        next_level: list[uuid.UUID] = []
        for step_id in current:
            for dependent in dependents[step_id]:
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    next_level.append(dependent)

        current = sorted(next_level, key=lambda step_id: index_of[step_id])

    if processed != len(steps):
        return None

    return levels


def _aggregate_coding_step(steps: list[PlanStepModel]) -> PlanStepModel:
    """
    Creates a single aggregated coding step that combines the objectives, files, and tests of the given steps.

    Args:
        steps: A list of PlanStepModel to aggregate.

    Returns:
        A single PlanStepModel that represents the aggregate of the given steps.
    """

    objectives: list[str] = []
    files: list[str] = []
    tests: list[str] = []

    for step in steps:
        if step.objective.strip():
            objectives.append(step.objective.strip())
        files.extend(file for file in step.files if file.strip())
        tests.extend(test for test in step.tests if test.strip())

    return PlanStepModel(
        title="Aggregated coding steps",
        objective="\n".join(objectives) or "Implement planned changes",
        files=list(dict.fromkeys(files)),
        tests=list(dict.fromkeys(tests)),
    )


class CodeStep(PipelineStep):
    stage = PipelineStage.CODE
    success_state = RunState.CODING.value

    @traced("pipeline.code_step", attributes=pipeline_step_attrs)
    def execute(self, context: dict[str, Any], run: RunModel, runtime: StepRuntime) -> StageResult:
        steps = context.get("steps", [])
        if not isinstance(steps, list) or not steps:
            return StageResult(
                stage=self.stage,
                status=StageStatus.BLOCKED,
                outputs={"files_map": {}, "tests_map": {}},
                notes={"reason": "No plan steps available for coding"},
            )

        try:
            plan_steps = [PlanStepModel(**raw_step) for raw_step in steps]
        except Exception as exc:
            return StageResult(
                stage=self.stage,
                status=StageStatus.BLOCKED,
                outputs={"files_map": {}, "tests_map": {}},
                notes={"reason": f"Invalid plan step for coding ({exc})."},
            )

        levels = _dependency_levels(plan_steps)
        if levels is None:
            return StageResult(
                stage=self.stage,
                status=StageStatus.BLOCKED,
                outputs={"files_map": {}, "tests_map": {}},
                notes={"reason": "Cyclic dependencies between plan steps; cannot order coding."},
            )

        repo_map = context.get("repo_map", "")
        file_contents = context.get("file_contents", {})
        if not isinstance(repo_map, str):
            repo_map = ""
        if not isinstance(file_contents, dict):
            file_contents = {}
        typed_file_contents: dict[str, str] = {}
        for path, content in file_contents.items():
            if not isinstance(path, str) or not isinstance(content, str):
                continue
            typed_file_contents[path] = content

        # Load existing contents of the planned target files from the checkout so the
        # code agent edits real files instead of regenerating them blind.
        repo_path = context.get("repo_path")
        if isinstance(repo_path, str) and repo_path:
            targets: list[str] = []
            for plan_step in plan_steps:
                targets.extend(plan_step.files)
                targets.extend(plan_step.tests)
            for path, content in read_target_files(repo_path, targets).items():
                typed_file_contents.setdefault(path, content)

        qa_feedback = context.get("qa_feedback", "")
        if not isinstance(qa_feedback, str):
            qa_feedback = ""

        # On a QA retry, overlay the previous attempt's generated files so the
        # model fixes its last output instead of regenerating from scratch.
        if qa_feedback.strip():
            previous_output = context.get("coding_output")
            if isinstance(previous_output, dict):
                for source_key in ("files_map", "tests_map"):
                    prior_files = previous_output.get(source_key)
                    if isinstance(prior_files, dict):
                        for path, content in prior_files.items():
                            if isinstance(path, str) and isinstance(content, str):
                                typed_file_contents[path] = content

        # Files produced by earlier levels, fed to later levels as context.
        generated_so_far: dict[str, str] = {}
        aggregate_files_map: dict[str, str] = {}
        aggregate_tests_map: dict[str, str] = {}
        coding_order: list[str] = []

        for level in levels:
            # Steps in a level are independent: dispatch them all at once and
            # only fold their outputs into context after the level completes.
            trace_context = inject_trace_context()
            dispatched = [
                (
                    plan_step,
                    CodeWorker.remote().run.remote(
                        CodeWorkerInput(
                            step=plan_step,
                            repo_map=repo_map,
                            file_contents=typed_file_contents,
                            dependency_files=dict(generated_so_far),
                            qa_feedback=qa_feedback,
                        ),
                        trace_context=trace_context,
                    ),
                )
                for plan_step in level
            ]

            results = ray.get([ref for _, ref in dispatched])

            level_generated: dict[str, str] = {}
            for (plan_step, _), worker_result in zip(dispatched, results):
                status, output = worker_result
                code_output = output if isinstance(output, dict) else {}
                try:
                    normalized = CodeOutputModel(**code_output)
                except Exception:
                    normalized = CodeOutputModel()

                if not is_success_status(status):
                    return StageResult(
                        stage=self.stage,
                        status=status,
                        outputs={
                            "coding_output": CodeOutputModel(
                                files_map=aggregate_files_map,
                                tests_map=aggregate_tests_map,
                            ).model_dump(),
                            "coding_order": coding_order,
                        },
                        notes={"reason": f"Coding step '{plan_step.title}' did not succeed."},
                    )

                for path, content in normalized.files_map.items():
                    level_generated[path] = content
                    aggregate_files_map[path] = content
                for path, content in normalized.tests_map.items():
                    level_generated[path] = content
                    aggregate_tests_map[path] = content

                coding_order.append(str(plan_step.id))

            generated_so_far.update(level_generated)

        aggregate_output = CodeOutputModel(
            files_map=aggregate_files_map,
            tests_map=aggregate_tests_map,
        ).model_dump()

        return StageResult(
            stage=self.stage,
            status=StageStatus.OK,
            outputs={
                "coding_order": coding_order,
                "coding_step": _aggregate_coding_step(
                    [s for level in levels for s in level]
                ).model_dump(),
                "coding_output": aggregate_output,
            },
        )
