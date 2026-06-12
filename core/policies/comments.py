from typing import Any

from core.policies.engine import PolicyFinding


def _finding_from_mapping(item: dict[str, Any]) -> PolicyFinding:
    return PolicyFinding(
        reason=str(item.get("reason") or item.get("summary") or "Review finding").strip(),
        suggested_fix=str(
            item.get("suggested_fix") or "Review and address this finding before merging."
        ).strip(),
        internal_code=str(item.get("internal_code") or item.get("category") or "").strip(),
    )


def normalize_public_findings(value: Any) -> list[PolicyFinding]:
    if not isinstance(value, list):
        return []

    findings: list[PolicyFinding] = []
    for item in value:
        if isinstance(item, PolicyFinding):
            findings.append(item)
        elif isinstance(item, dict):
            findings.append(_finding_from_mapping(item))
    return findings


def format_review_findings_comment(
    *,
    title: str = "AutoPR did not merge this pull request.",
    findings: list[PolicyFinding],
    fallback: str = "A reviewer should inspect the change before merging.",
) -> str:
    lines = [title.strip(), ""]
    if not findings:
        lines.append(fallback.strip())
        return "\n".join(lines)

    lines.append("Findings:")
    for finding in findings:
        reason = finding.reason.strip() or "Review finding"
        suggested_fix = (
            finding.suggested_fix.strip() or "Review and address this finding before merging."
        )
        lines.append(f"- {reason}")
        lines.append(f"  Suggested fix: {suggested_fix}")
    return "\n".join(lines)
