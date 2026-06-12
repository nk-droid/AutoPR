import logging
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Mapping

import yaml
from pydantic import BaseModel, Field

from core.contracts.enums import PipelineStage, RiskLevel
from core.orchestrator.models import MergeDecision, StageResult

logger = logging.getLogger(__name__)


class PolicyFinding(BaseModel):
    """Reviewer-facing policy finding with an optional internal reason code."""

    reason: str
    suggested_fix: str
    internal_code: str = ""


class PolicyConfig(BaseModel):
    """Configuration that controls deterministic merge policy checks."""

    block_high_risk_automerge: bool = True
    sensitive_path_patterns: list[str] = Field(default_factory=list)


class PolicyEvaluation(BaseModel):
    """Policy decision paired with findings safe to show to reviewers."""

    decision: MergeDecision
    public_findings: list[PolicyFinding] = Field(default_factory=list)


_POLICY_PATH = Path(__file__).resolve().parents[2] / "configs" / "policies.yaml"


def load_policy_config(path: Path = _POLICY_PATH) -> PolicyConfig:
    """
    Load merge policy settings, falling back to safe defaults when absent.

    Args:
        path: YAML policy path to load.

    Returns:
        Validated policy configuration.
    """

    if not path.exists():
        logger.warning(
            "policy config not found; using defaults",
            extra={"event": "config_missing", "config": "policies", "path": str(path)},
        )
        return PolicyConfig()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        config = PolicyConfig.model_validate(raw)
    except Exception as exc:
        logger.error(
            "policy config load failed",
            extra={
                "event": "config_load_failed",
                "config": "policies",
                "path": str(path),
                "error": exc.__class__.__name__,
            },
        )
        raise
    logger.debug(
        "policy config loaded",
        extra={"event": "config_loaded", "config": "policies", "path": str(path)},
    )
    return config


def coerce_merge_decision(value: Any) -> MergeDecision | None:
    """
    Normalize optional policy input into a merge decision model.

    Args:
        value: Existing merge decision or dictionary payload.

    Returns:
        Merge decision when coercion succeeds, otherwise None.
    """

    if isinstance(value, MergeDecision):
        return value
    if isinstance(value, dict):
        try:
            return MergeDecision(**value)
        except Exception:
            return None
    return None


def _risk_levels(context: Mapping[str, Any]) -> list[str]:
    """
    Extract risk levels from triage and planning context.

    Args:
        context: Workflow context containing risk and plan data.

    Returns:
        Lowercase risk levels found in the context.
    """

    levels: list[str] = []

    risk = context.get("risk")
    if isinstance(risk, dict):
        level = risk.get("level")
        if isinstance(level, str):
            levels.append(level.lower())

    steps = context.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, dict):
                continue
            level = step.get("risk_level")
            if isinstance(level, str):
                levels.append(level.lower())

    return levels


def _changed_paths(context: Mapping[str, Any]) -> set[str]:
    """
    Collect changed file paths reported by review and coding stages.

    Args:
        context: Workflow context containing changed files or coding output.

    Returns:
        Set of changed paths considered by policy checks.
    """

    paths: set[str] = set()

    changed_files = context.get("changed_files")
    if isinstance(changed_files, list):
        for item in changed_files:
            if isinstance(item, dict):
                filename = item.get("filename")
                if isinstance(filename, str) and filename:
                    paths.add(filename)

    coding_output = context.get("coding_output")
    if isinstance(coding_output, dict):
        for key in ("files_map", "tests_map", "files"):
            values = coding_output.get(key)
            if isinstance(values, dict):
                paths.update(path for path in values if isinstance(path, str) and path)

    return paths


def _stage_result(context: Mapping[str, Any], stage: PipelineStage) -> StageResult | None:
    """
    Retrieve a stage result from workflow context when available.

    Args:
        context: Workflow context containing prior stage results.
        stage: Pipeline stage whose result should be loaded.

    Returns:
        Stage result model, or None when missing or invalid.
    """

    stage_results = context.get("_stage_results")
    if not isinstance(stage_results, dict):
        return None
    value = stage_results.get(stage.value) or stage_results.get(stage)
    if isinstance(value, StageResult):
        return value
    if isinstance(value, dict):
        try:
            return StageResult(**value)
        except Exception:
            return None
    return None


def _qa_findings(context: Mapping[str, Any]) -> list[PolicyFinding]:
    """
    Convert non-green QA results into merge-blocking policy findings.

    Args:
        context: Workflow context containing prior QA stage results.

    Returns:
        Policy findings explaining QA readiness issues.
    """

    qa_result = _stage_result(context, PipelineStage.QA)
    if qa_result is None:
        return []
    if qa_result.status.value == "ok":
        return []
    return [
        PolicyFinding(
            internal_code="qa_not_ready",
            reason="The pull request is not ready for merge because required validation has not completed successfully.",
            suggested_fix="Review the latest validation feedback, update the branch, and rerun the checks before merging.",
        )
    ]


def evaluate_review_policy(context: Mapping[str, Any]) -> PolicyEvaluation:
    """
    Evaluate deterministic policy gates before allowing automated merge.

    Args:
        context: Workflow context with risk, file, and QA information.

    Returns:
        Policy decision and reviewer-safe findings.
    """

    config = load_policy_config()
    findings: list[PolicyFinding] = []

    if config.block_high_risk_automerge and RiskLevel.HIGH.value in _risk_levels(context):
        findings.append(
            PolicyFinding(
                internal_code="risk_high",
                reason="This change is high risk and needs human review before merge.",
                suggested_fix="Have a reviewer verify the implementation approach, affected behavior, and rollout safety.",
            )
        )

    changed_paths = _changed_paths(context)
    for path in sorted(changed_paths):
        if any(fnmatch(path, pattern) for pattern in config.sensitive_path_patterns):
            findings.append(
                PolicyFinding(
                    internal_code="sensitive_path_changed",
                    reason=f"The pull request changes a sensitive area: `{path}`.",
                    suggested_fix="Have an owner review the change and confirm it is safe to merge.",
                )
            )
            break

    findings.extend(_qa_findings(context))

    if findings:
        return PolicyEvaluation(
            decision=MergeDecision(
                allowed=False,
                reason="Policy checks blocked merge",
                blocking_reasons=[
                    finding.internal_code for finding in findings if finding.internal_code
                ],
            ),
            public_findings=findings,
        )

    return PolicyEvaluation(decision=MergeDecision(allowed=True, reason="Policy checks passed"))
