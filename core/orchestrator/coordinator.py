import os
import ray
import time
from typing import Any

from core.contracts.enums import PipelineStage, RunState
from core.contracts.run_context import IssueToPRContext, PRToMergeContext
from core.orchestrator.models import RunModel, RunType, StageResult, StageStatus
from core.orchestrator.state_machine import StateMachine
from core.orchestrator.steps.base import is_success_status
from core.orchestrator.steps.registry import steps_for_run_type
from core.orchestrator.transitions import can_transition
from infra.storage.artifacts import record_run_event, save_artifact, upsert_run
from infra.storage.review_requests import attach_review_request_slack_ref, create_review_request
from infra.slack.notification import send_needs_review_notification

from observability.metrics import observe_run, observe_stage
from observability.tracing import get_tracer, inject_trace_context

import dotenv
dotenv.load_dotenv()

class Coordinator:
    def __init__(self, run: RunModel | None = None) -> None:
        self.run = run or RunModel(state=RunState.RECEIVED.value)
        self.state_machine = StateMachine(
            initial_state=self.run.state,
            run_type=self.run.run_type,
        )

        # Record the run before any stage can mutate pipeline state.
        self._persist_run(event_type="run_initialized", payload={"state": self.run.state})

    def _persist_run(self, *, event_type: str | None = None, payload: dict[str, Any] | None = None) -> None:
        """
        Store the current run snapshot and optionally append an audit event.

        Args:
            event_type: Optional timeline event name to record with the snapshot.
            payload: Optional structured event payload for debugging and recovery.
        """

        run_id = str(self.run.run_id)
        run_type = self.run.run_type.value if hasattr(self.run.run_type, "value") else str(self.run.run_type)
        run_payload = self.run.model_dump(mode="json")

        # Persist run snapshots so recovery sees the latest pipeline state.
        upsert_run(
            run_id=run_id,
            state=self.run.state,
            run_type=run_type,
            payload=run_payload,
        )
        if event_type:
            # Event log stays append-only so run timelines remain auditable.
            record_run_event(run_id, event_type, payload or {})

    def run_once(self, *, decision: str | None = None, reason: str = "") -> str:
        """
        Apply one optional state decision and return the resulting state.

        Args:
            decision: Optional target state requested by a caller.
            reason: Human-readable reason stored with the transition.

        Returns:
            The current state after applying the optional decision.
        """

        if decision:
            self.transition_to(decision, reason=reason)
        return self.state_machine.state

    def transition_to(
        self,
        next_state: str,
        *,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Move the run through the validated state machine and persist history.

        Args:
            next_state: State requested by the pipeline step or caller.
            reason: Business reason saved with the transition event.
            metadata: Extra structured context for audit and debugging.

        Returns:
            The state accepted by the state machine.
        """

        # Always compute the next state from the state machine rather than trusting the caller.
        state = self.state_machine.transition(next_state, reason=reason, metadata=metadata)
        self.run.state = state
        self.run.transition_history = list(self.state_machine.history)
        self._persist_run(
            event_type="state_transition",
            payload={
                "state": state,
                "reason": reason,
                "metadata": metadata or {},
            },
        )
        return state

    def _block_run(self, *, reason: str, metadata: dict[str, Any] | None = None) -> str:
        """
        Stop the run in BLOCKED when autonomous execution cannot continue.

        Args:
            reason: Machine-readable blocking reason for operators and recovery.
            metadata: Extra context explaining where execution stopped.

        Returns:
            The terminal BLOCKED state.
        """

        # Terminal stops write a dedicated blocked event even when the workflow
        # is already sitting at a terminal or retry-exhausted state.
        state = self.state_machine.transition(
            RunState.BLOCKED.value, reason=reason, metadata=metadata or {}, validate=False
        )
        self.run.state = state
        self.run.transition_history = list(self.state_machine.history)
        self._persist_run(
            event_type="run_blocked",
            payload={"reason": reason, "metadata": metadata or {}},
        )
        return state

    def set_run_type(self, run_type: RunType) -> None:
        """
        Select the workflow graph that governs all subsequent transitions.

        Args:
            run_type: Pipeline workflow type selected for this run.
        """

        # Align state-machine rules with the selected workflow type
        self.run.run_type = run_type
        self.state_machine.set_run_type(run_type)
        self._persist_run(
            event_type="run_type_set",
            payload={"run_type": run_type.value if hasattr(run_type, "value") else str(run_type)},
        )

    def add_stage_result(self, result: StageResult) -> StageResult:
        """
        Append a stage result to run history and persist it as an artifact.

        Args:
            result: Completed stage result produced by a pipeline step.

        Returns:
            The same result after persistence succeeds.
        """

        # Save stage result to run history for debugging.
        self.run.stage_results.append(result)
        run_id = str(self.run.run_id)
        stage_name = result.stage
        stage_status = result.status.value if hasattr(result.status, "value") else str(result.status)
        artifact_key = f"stage_result:{len(self.run.stage_results) - 1}:{stage_name}"
        save_artifact(
            run_id,
            artifact_key,
            {
                "stage": stage_name,
                "status": stage_status,
                "outputs": result.outputs if isinstance(result.outputs, dict) else {},
                "notes": result.notes if isinstance(result.notes, dict) else {},
            },
        )
        self._persist_run(
            event_type="stage_result",
            payload={"stage": stage_name, "status": stage_status},
        )
        return result

    def _seed_context_from_run(self, context: dict[str, Any]) -> None:
        """
        Fill missing workflow context fields from the current run model.

        Args:
            context: Mutable pipeline context shared across stage execution.
        """

        context["repository"] = context.get("repository") or self.run.repository
        context["issue_number"] = context.get("issue_number") or self.run.issue_number
        context["pull_request_number"] = context.get("pull_request_number") or self.run.pull_request_number
        context["metadata"] = context.get("metadata") or self.run.metadata

    def _sync_run_from_context(self, context: dict[str, Any]) -> None:
        """
        Copy workflow identifiers from stage context back onto the run model.

        Args:
            context: Mutable pipeline context that may contain new PR metadata.
        """

        self.run.repository = context.get("repository", "")
        self.run.issue_number = context.get("issue_number")
        self.run.pull_request_number = context.get("pull_request_number")
        self.run.metadata = context.get("metadata", {})

    def run_worker(self, stage: PipelineStage, worker: Any, *args: Any) -> StageResult:
        """
        Execute a Ray worker and normalize its output into a stage result.

        Args:
            stage: Pipeline stage represented by the worker call.
            worker: Ray actor or remote worker exposing a run method.
            *args: Positional payload passed to the worker.

        Returns:
            Stage result containing worker status and structured outputs.
        """

        trace_context = inject_trace_context()
        worker_result_ref = worker.run.remote(*args, trace_context=trace_context)
        stage_status, worker_result = ray.get(worker_result_ref)
        outputs = worker_result if isinstance(worker_result, dict) else {"value": worker_result}
        return StageResult(
            stage=stage.value,
            status=stage_status,
            outputs=outputs,
        )
    
    @staticmethod
    def _first_stage_index(steps: list[Any], stage: PipelineStage) -> int | None:
        """
        Find the first step index for a stage used by retry routing.

        Args:
            steps: Ordered pipeline steps for the active run type.
            stage: Pipeline stage to locate in the step list.

        Returns:
            The first matching index, or None when the stage is absent.
        """

        for index, step in enumerate(steps):
            if getattr(step, "stage", None) == stage:
                return index
        return None

    @staticmethod
    def _format_qa_feedback(result: StageResult) -> str:
        """
        Convert failed QA outputs into concise feedback for the coding retry.

        Args:
            result: QA stage result containing summary, notes, or checks.

        Returns:
            Newline-delimited feedback for the next coding attempt.
        """

        outputs = result.outputs if isinstance(result.outputs, dict) else {}
        lines: list[str] = []

        summary = outputs.get("summary")
        if isinstance(summary, str) and summary.strip():
            lines.append(summary.strip())

        notes = outputs.get("notes")
        if isinstance(notes, dict):
            aggregate_summary = notes.get("aggregate_summary")
            if isinstance(aggregate_summary, str) and aggregate_summary.strip():
                lines.append(aggregate_summary.strip())

        checks = outputs.get("checks")
        if isinstance(checks, list):
            for check in checks:
                if not isinstance(check, dict):
                    continue
                status = str(check.get("status", "")).lower()
                if status in {"fail", "warn"}:
                    name = check.get("name", "check")
                    details = check.get("details", "")
                    lines.append(f"- {name} [{status}]: {details}")

        return "\n".join(lines)
    
    def _create_review_request(
        self,
        *,
        step_index: int,
        result: StageResult,
        context: dict[str, Any],
    ) -> None:
        """
        Create a resumable human-review gate for publish or soft-risk stages.

        Args:
            step_index: Pipeline step index to resume after approval.
            result: Stage result that requested human review.
            context: Current pipeline context to store with the request.
        """

        stage_name = result.stage.value if hasattr(result.stage, "value") else str(result.stage)

        # Build the review context needed to resume from this gate
        review_context = {
            "repository": context.get("repository") or self.run.repository,
            "issue_number": context.get("issue_number") or self.run.issue_number,
            "pull_request_number": context.get("pull_request_number") or self.run.pull_request_number,
            "metadata": context.get("metadata") if isinstance(context.get("metadata"), dict) else dict(self.run.metadata),
            "head_branch": context.get("head_branch"),
            "base_branch": context.get("base_branch"),
            "execute_remote_actions": bool(context.get("execute_remote_actions", False)),
            "review_approved": bool(context.get("review_approved", False)),
            "_resume_stage_index": step_index,
        }

        # Copy across review-relevant fields from the context so the next run picks them up automatically.
        for key in (
            "review_request_kind",
            "llm_review",
            "merge_risk",
            "confidence",
            "blocking_findings",
            "risk",
            "steps",
            "coding_output",
            "qa_output",
            "changed_files",
            "policy_public_findings",
            "policy_decision",
        ):
            value = context.get(key)
            if value is not None:
                review_context[key] = value

        review_request = create_review_request(
            run_id=str(self.run.run_id),
            run_type=self.run.run_type.value if hasattr(self.run.run_type, "value") else str(self.run.run_type),
            stage=stage_name,
            stage_index=step_index,
            context=review_context,
        )

        # Notify reviewers (Slack first; GitHub comes from the review-request sync logic)
        slack_result = send_needs_review_notification(self.run, result, review_request)
        message_ref = slack_result.get("message_ref")
        if isinstance(message_ref, str) and message_ref.strip():
            attach_review_request_slack_ref(review_request["request_id"], message_ref)

        self._persist_run(
            event_type="needs_review_raised",
            payload={
                "request_id": review_request["request_id"],
                "stage": stage_name,
                "stage_index": step_index,
                "slack_sent": bool(slack_result.get("sent", False)),
                "slack_reason": str(slack_result.get("reason", "")),
            },
        )

    def _run_steps(self, context: dict[str, Any]) -> RunModel:
        """
        Execute the selected workflow steps while handling retry and review gates.

        Args:
            context: Mutable workflow context shared by all pipeline steps.

        Returns:
            The run model after all executable steps have completed or stopped.
        """

        self._seed_context_from_run(context)
        self._sync_run_from_context(context)

        stage_results = context.get("_stage_results")
        if not isinstance(stage_results, dict):
            stage_results = {}
            context["_stage_results"] = stage_results

        # Make previous stages available in the context
        for previous in self.run.stage_results:
            previous_stage_name = previous.stage.value if hasattr(previous.stage, "value") else str(previous.stage)
            stage_results[previous_stage_name] = previous
            if isinstance(previous.outputs, dict):
                for key, value in previous.outputs.items():
                    context.setdefault(key, value)

        qa_max_retries = int(os.getenv("QA_MAX_RETRIES", "1"))
        raw_retry_count = context.get("qa_retry_count", 0)
        try:
            qa_retry_count = int(raw_retry_count)
        except (TypeError, ValueError):
            qa_retry_count = 0

        context["qa_retry_count"] = qa_retry_count
        context["qa_max_retries"] = qa_max_retries

        steps = steps_for_run_type(self.run.run_type)
        code_stage_index = self._first_stage_index(steps, PipelineStage.CODE)

        # Resume from where we left off
        raw_resume_index = context.get("_resume_stage_index", 0)
        try:
            step_index = int(raw_resume_index)
        except (TypeError, ValueError):
            step_index = 0
        if step_index < 0 or step_index >= len(steps):
            step_index = 0

        # Hard cap on total step executions so backward jumps (e.g. QA retries)
        # can never spin forever; the run is BLOCKED once the cap is hit.
        max_autonomous_loops = int(os.getenv("AUTOPR_MAX_AUTONOMOUS_LOOPS", "50"))
        loop_count = 0
        while step_index < len(steps):
            loop_count += 1
            if loop_count > max_autonomous_loops:
                self._block_run(
                    reason="max_autonomous_loops_reached",
                    metadata={
                        "max_autonomous_loops": max_autonomous_loops,
                        "stage_index": step_index,
                        "stage": str(getattr(steps[step_index], "stage", "")),
                    },
                )
                break

            # Let each step declare its own pre-flight checks and transitions
            step = steps[step_index]
            for next_state, reason in step.before(context, self.run):
                self.transition_to(next_state, reason=reason)

            started_at = time.perf_counter()
            result = step.execute(context, self.run, self)
            observe_stage(
                self.run.run_type,
                step.stage,
                result.status,
                time.perf_counter() - started_at,
            )

            # Persist result and update context
            result = self.add_stage_result(result)
            stage_results[str(result.stage)] = result

            if isinstance(result.outputs, dict):
                context.update(result.outputs)
            self._sync_run_from_context(context)

            # Apply post stage transitions
            for next_state, reason in step.after(result, context, self.run):
                self.transition_to(next_state, reason=reason or str(step.stage.value))

            # Pause publish or soft-risk flows for human review
            if (
                result.status == StageStatus.NEEDS_REVIEW
                and (
                    getattr(step, "stage", None) == PipelineStage.PUBLISH
                    or result.notes.get("review_request_kind") == "llm_soft_gate"
                )
            ):
                self._create_review_request(
                    step_index=step_index,
                    result=result,
                    context=context
                )

            # Stop or retry on failure
            if not is_success_status(result.status):
                is_qa_step = getattr(step, "stage", None) == PipelineStage.QA
                can_retry_qa = (
                    is_qa_step
                    and code_stage_index is not None
                    and qa_retry_count < qa_max_retries
                )

                # Fail back to code and retry if the stage allows
                if can_retry_qa:
                    qa_retry_count += 1
                    context["qa_retry_count"] = qa_retry_count
                    context["qa_feedback"] = self._format_qa_feedback(result)

                    retry_metadata = {
                        "qa_retry_count": qa_retry_count,
                        "qa_max_retries": qa_max_retries,
                        "qa_status": result.status.value
                    }

                    if can_transition(self.state_machine.state, RunState.QA_RUNNING.value, self.run.run_type):
                        self.transition_to(
                            RunState.QA_RUNNING.value,
                            reason="qa_failed_retry_pending",
                            metadata=retry_metadata
                        )

                    self.transition_to(
                        RunState.CODING.value,
                        reason=f"qa_retry_{qa_retry_count}",
                        metadata=retry_metadata,
                    )
                    self._persist_run(event_type="qa_retry_scheduled", payload=retry_metadata)

                    step_index = code_stage_index
                    continue

                break

            step_index += 1

        return self.run

    def run_issue_to_pr(self, context: IssueToPRContext) -> RunModel:
        """
        Run the full issue-to-pull-request workflow for a GitHub issue.

        Args:
            context: Validated issue workflow inputs and execution flags.

        Returns:
            Final run model after triage, coding, QA, publish, and PR steps.
        """

        self.set_run_type(RunType.ISSUE_TO_PR)

        with get_tracer().start_as_current_span(
            "autopr.run",
            attributes={
                "autopr.run_id": str(self.run.run_id),
                "autopr.run_type": self.run.run_type.value,
                "autopr.repository": context.repository,
                "autopr.issue_number": context.issue_number,
            }
        ) as span:
            final_run = self._run_steps(context.model_dump(mode="json"))
            span.set_attribute("autopr.final_state", final_run.state)
            observe_run(final_run.run_type, final_run.state)
            return final_run

    def run_pr_to_merge(self, context: PRToMergeContext) -> RunModel:
        """
        Run the review-to-merge workflow for an existing pull request.

        Args:
            context: Validated PR workflow inputs and approval state.

        Returns:
            Final run model after review and merge processing.
        """

        self.set_run_type(RunType.PR_TO_MERGE)

        with get_tracer().start_as_current_span(
            "autopr.run",
            attributes={
                "autopr.run_id": str(self.run.run_id),
                "autopr.run_type": self.run.run_type.value,
                "autopr.repository": context.repository,
                "autopr.pull_request_number": context.pull_request_number,
            }
        ) as span:
            final_run = self._run_steps(context.model_dump(mode="json"))
            span.set_attribute("autopr.final_state", final_run.state)
            observe_run(final_run.run_type, final_run.state)
            return final_run

if __name__ == "__main__":
    import json
    import time
    import uuid

    started_at = time.time()
    run_model = RunModel(
        run_id=uuid.uuid4(),
        state=RunState.RECEIVED.value,
        run_type=RunType.ISSUE_TO_PR,
        repository="nk-droid/test",
        issue_number=1,
        pull_request_number=None,
        metadata={},
        stage_results=[],
        transition_history=[],
    )

    context = {
        "repository": run_model.repository,
        "issue_number": run_model.issue_number,
        "pull_request_number": run_model.pull_request_number,
        "metadata": run_model.metadata,
        "head_branch": "autopr/issue-1",   # required
        "base_branch": "main",             # optional (defaults to main)
        "execute_remote_actions": True,    # needed to actually open PR via API
    }
    coordinator = Coordinator(run_model)
    issue_to_pr_run = coordinator.run_issue_to_pr(context=IssueToPRContext(**context))

    raw_pr_number = context.get("pull_request_number")
    if isinstance(raw_pr_number, int):
        created_pr_number = raw_pr_number
    elif isinstance(raw_pr_number, str) and raw_pr_number.strip().isdigit():
        created_pr_number = int(raw_pr_number.strip())
    else:
        created_pr_number = issue_to_pr_run.pull_request_number

    final_run = issue_to_pr_run
    if issue_to_pr_run.state == RunState.PR_OPENED.value and created_pr_number is not None:
        context["pull_request_number"] = created_pr_number
        context["review_approved"] = True  # demo mode: allow merge workflow to proceed.
        final_run = coordinator.run_pr_to_merge(context=PRToMergeContext(**context))
    else:
        print("Skipping PR-to-merge workflow: PR was not opened in issue-to-PR run.")

    with open("result.json", "w", encoding="utf-8") as f:
        f.write(final_run.model_dump_json(indent=4))

    print(f"Final state: {final_run.state}")
    print(f"Total time: {time.time() - started_at}")
