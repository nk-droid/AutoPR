import os
from typing import Any

from core.contracts.enums import PipelineStage, RunState
from core.contracts.run_context import PRToMergeContext, ReviewWorkerInput
from core.orchestrator.models import MergeDecision, RunModel, StageResult, StageStatus
from core.orchestrator.steps.base import PipelineStep, StepRuntime, is_success_status
from core.orchestrator.transitions import can_merge_pr
from core.policies.comments import format_review_findings_comment
from core.policies.engine import PolicyFinding, coerce_merge_decision, evaluate_review_policy

from infra.github.client import GitHubAPIError, GitHubClient

from infra.ray.actors import ReviewWorker
from observability.tracing import pipeline_step_attrs, traced

_SOFT_GATE_RISKS = {"medium", "high"}


def _compact_pull_request_files(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in files:
        filename = item.get("filename")
        if not isinstance(filename, str) or not filename:
            continue
        patch = item.get("patch")
        compact.append(
            {
                "filename": filename,
                "status": item.get("status", ""),
                "additions": item.get("additions", 0),
                "deletions": item.get("deletions", 0),
                "changes": item.get("changes", 0),
                "patch": patch[:4000] if isinstance(patch, str) else "",
            }
        )
    return compact


def _external_policy_findings(decision: MergeDecision | None) -> list[PolicyFinding]:
    if decision is None or decision.allowed:
        return []
    return [
        PolicyFinding(
            internal_code="external_policy_denied",
            reason=decision.reason or "A merge policy blocked this pull request.",
            suggested_fix="Review the policy feedback and update the pull request before merging.",
        )
    ]


def _choose_policy_decision(
    *,
    computed: MergeDecision,
    external: MergeDecision | None,
) -> MergeDecision:
    if external is not None and not external.allowed:
        return external
    return computed


def _comment_on_pr(
    *,
    context: dict[str, Any],
    title: str,
    findings: list[PolicyFinding],
    fallback: str,
) -> str:
    repository = context.get("repository")
    pull_request_number = context.get("pull_request_number")
    if not isinstance(repository, str) or not repository:
        return "missing_repository"
    if not isinstance(pull_request_number, int):
        return "missing_pull_request_number"

    body = format_review_findings_comment(title=title, findings=findings, fallback=fallback)
    client = GitHubClient(token=context.get("github_token") or os.environ.get("GITHUB_TOKEN"))
    try:
        client.comment_on_pull_request(repo=repository, pull_number=pull_request_number, body=body)
    except Exception as exc:
        return str(exc)
    finally:
        client.close()
    return ""


def _review_block_findings(result: StageResult) -> list[PolicyFinding]:
    outputs = result.outputs if isinstance(result.outputs, dict) else {}
    required_actions = outputs.get("required_actions")
    if not isinstance(required_actions, list):
        required_actions = []
    summary = outputs.get("summary")
    findings: list[PolicyFinding] = []
    for action in required_actions:
        if isinstance(action, str) and action.strip():
            findings.append(
                PolicyFinding(
                    internal_code="review_not_ready",
                    reason="The pull request is not ready for merge.",
                    suggested_fix=action.strip(),
                )
            )
    if not findings and isinstance(summary, str) and summary.strip():
        findings.append(
            PolicyFinding(
                internal_code="review_not_ready",
                reason=summary.strip(),
                suggested_fix="Review the pull request status and address the listed concerns before merging.",
            )
        )
    return findings


class ReviewStep(PipelineStep):
    stage = PipelineStage.REVIEW

    def before(self, context: dict[str, Any], run: RunModel) -> list[tuple[str, str]]:
        if run.state in {RunState.RECEIVED.value, RunState.PR_OPENED.value}:
            return [(RunState.REVIEW_PENDING.value, "start merge workflow")]
        return []

    @traced("pipeline.review_step", attributes=pipeline_step_attrs)
    def execute(self, context: dict[str, Any], run: RunModel, runtime: StepRuntime) -> StageResult:
        repository = context.get("repository") or run.repository
        pull_request_number = context.get("pull_request_number")
        if pull_request_number is None:
            pull_request_number = run.pull_request_number

        if not repository or pull_request_number is None:
            return StageResult(
                stage=self.stage,
                status=StageStatus.BLOCKED,
                notes={
                    "reason": "Review blocked: repository or pull_request_number missing.",
                    "blocking_reasons": ["missing_review_inputs"],
                },
            )

        context["repository"] = repository
        context["pull_request_number"] = pull_request_number

        client = GitHubClient(token=context.get("github_token") or os.environ.get("GITHUB_TOKEN"))
        pull_request_files: list[dict[str, Any]] = []
        try:
            pull_request = client.get_pull_request(repository, pull_request_number)
            try:
                pull_request_files = client.list_pull_request_files(repository, pull_request_number)
            except Exception as exc:
                context["pull_request_files_error"] = str(exc)
        except Exception as exc:
            notes: dict[str, Any] = {
                "reason": "Review blocked: failed to load pull request details.",
                "blocking_reasons": ["pull_request_fetch_failed"],
                "error": str(exc),
                "repository": repository,
                "pull_request_number": pull_request_number,
            }
            if isinstance(exc, GitHubAPIError):
                notes["status_code"] = exc.status_code
                notes["api_error_payload"] = exc.response_payload
            return StageResult(
                stage=self.stage,
                status=StageStatus.BLOCKED,
                notes=notes,
            )
        finally:
            client.close()

        # Refresh PR metadata from GitHub so review logic uses canonical state.
        context["pull_request_url"] = pull_request.get("html_url") or context.get(
            "pull_request_url"
        )
        context["pull_request_state"] = pull_request.get("state")
        context["pull_request_draft"] = bool(pull_request.get("draft", False))
        mergeable = pull_request.get("mergeable")
        if mergeable is None or isinstance(mergeable, bool):
            context["pull_request_mergeable"] = mergeable
        context["pull_request_mergeable_state"] = pull_request.get("mergeable_state")
        context["changed_files"] = _compact_pull_request_files(pull_request_files)

        policy = evaluate_review_policy(context)
        external_policy_decision = coerce_merge_decision(context.get("policy_decision"))
        policy_decision = _choose_policy_decision(
            computed=policy.decision,
            external=external_policy_decision,
        )
        policy_findings = [
            *policy.public_findings,
            *_external_policy_findings(external_policy_decision),
        ]
        context["policy_decision"] = policy_decision.model_dump()
        context["policy_public_findings"] = [finding.model_dump() for finding in policy_findings]
        if not policy_decision.allowed:
            context["_merge_decision"] = policy_decision.model_dump()
            comment_error = _comment_on_pr(
                context=context,
                title="AutoPR did not merge this pull request.",
                findings=policy_findings,
                fallback="A hard merge policy blocked this pull request.",
            )
            notes = {
                "reason": policy_decision.reason,
                "blocking_reasons": policy_decision.blocking_reasons,
                "policy_public_findings": context["policy_public_findings"],
            }
            if comment_error:
                notes["comment_error"] = comment_error
            return StageResult(
                stage=self.stage,
                status=StageStatus.BLOCKED,
                notes=notes,
            )

        payload = dict(context)
        payload.setdefault("repository", repository)
        payload.setdefault("pull_request_number", pull_request_number)
        payload.setdefault("review_approved", bool(context.get("review_approved", False)))
        payload.setdefault(
            "execute_remote_actions", bool(context.get("execute_remote_actions", False))
        )
        payload.setdefault(
            "metadata",
            context.get("metadata")
            if isinstance(context.get("metadata"), dict)
            else dict(run.metadata),
        )
        review_context = PRToMergeContext(**payload)
        return runtime.run_worker(
            self.stage, ReviewWorker.remote(), ReviewWorkerInput(context=review_context)
        )

    def after(
        self,
        result: StageResult,
        context: dict[str, Any],
        run: RunModel,
    ) -> list[tuple[str, str]]:
        if result.status == StageStatus.BLOCKED:
            existing_decision = coerce_merge_decision(context.get("_merge_decision"))
            if existing_decision is None:
                decision = MergeDecision(
                    allowed=False,
                    reason="Review is not in mergeable state",
                    blocking_reasons=["review_not_green"],
                )
                context["_merge_decision"] = decision.model_dump()
            if isinstance(result.notes.get("policy_public_findings"), list):
                return []
            findings = _review_block_findings(result)
            comment_error = _comment_on_pr(
                context=context,
                title="AutoPR did not merge this pull request.",
                findings=findings,
                fallback="The pull request is not ready for merge.",
            )
            if comment_error:
                result.notes = {**result.notes, "comment_error": comment_error}
            return []

        if not is_success_status(result.status):
            return []

        policy_decision = coerce_merge_decision(context.get("policy_decision"))
        decision = can_merge_pr(result, policy_decision)
        context["_merge_decision"] = decision.model_dump()

        if not decision.allowed:
            result.status = StageStatus.BLOCKED
            result.notes = {
                **result.notes,
                "reason": decision.reason,
                "blocking_reasons": decision.blocking_reasons,
            }
            return []

        outputs = result.outputs if isinstance(result.outputs, dict) else {}
        llm_review = outputs.get("llm_review")
        if (
            isinstance(llm_review, dict)
            and str(llm_review.get("merge_risk", "")).lower() in _SOFT_GATE_RISKS
            and not bool(context.get("llm_soft_gate_approved", False))
        ):
            blocking_findings = llm_review.get("blocking_findings", [])
            if not isinstance(blocking_findings, list):
                blocking_findings = []
            context["llm_review"] = llm_review
            context["review_request_kind"] = "llm_soft_gate"
            context["merge_risk"] = llm_review.get("merge_risk", "")
            context["confidence"] = llm_review.get("confidence", "")
            context["blocking_findings"] = blocking_findings
            result.status = StageStatus.NEEDS_REVIEW
            result.notes = {
                **result.notes,
                "reason": "LLM merge-risk review requires human approval before merge.",
                "review_request_kind": "llm_soft_gate",
                "merge_risk": llm_review.get("merge_risk", ""),
                "confidence": llm_review.get("confidence", ""),
                "blocking_findings": blocking_findings,
            }
            return []

        return [(RunState.READY_TO_MERGE.value, decision.reason or "review checks passed")]
